from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from urllib.parse import urlparse, urlunparse

from chrome_tab_organizer.models import ChromeTab

LIVE_SNAPSHOT_TEXT_LIMIT = 50_000
LIVE_SNAPSHOT_FRAME_LIMIT = 10

WINDOW_COUNT_SCRIPT = r'''
tell application "Google Chrome"
    return count of windows
end tell
'''

CHROME_RUNNING_SCRIPT = r'''
application "Google Chrome" is running
'''

LIVE_SESSION_JS_DISABLED_MARKER = "Executing JavaScript through AppleScript is turned off"
WINDOW_INDEX_OUT_OF_RANGE_MARKER = "Window index out of range"
TAB_INDEX_OUT_OF_RANGE_MARKER = "Tab index out of range"
APPLE_EVENT_TIMEOUT_MARKER = "AppleEvent timed out"
AUTOMATION_NOT_AUTHORIZED_MARKERS = (
    "Not authorized to send Apple events",
    "not authorized to send apple events",
)


def get_chrome_window_count() -> int:
    result = subprocess.run(
        ["osascript", "-e", WINDOW_COUNT_SCRIPT],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(result.stdout.strip() or "0")


def is_chrome_running() -> bool:
    result = subprocess.run(
        ["osascript", "-e", CHROME_RUNNING_SCRIPT],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip().lower() == "true"


def preflight_chrome_access() -> tuple[bool, str | None]:
    try:
        if not is_chrome_running():
            return False, "Google Chrome is not running."
        window_count = get_chrome_window_count()
        if window_count < 1:
            return False, "Google Chrome is running but has no open windows."
        return True, None
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        return False, f"AppleScript could not access Google Chrome. {detail}"


def classify_live_session_error(detail: str) -> tuple[str, str]:
    message = detail.strip()
    if TAB_INDEX_OUT_OF_RANGE_MARKER in message:
        return ("tab_index_out_of_range", "Chrome tab index changed during extraction.")
    if WINDOW_INDEX_OUT_OF_RANGE_MARKER in message:
        return ("window_index_out_of_range", "Chrome window index changed during extraction.")
    if APPLE_EVENT_TIMEOUT_MARKER in message:
        return ("appleevent_timed_out", "Google Chrome automation timed out during live extraction.")
    if LIVE_SESSION_JS_DISABLED_MARKER in message:
        return (
            "javascript_from_apple_events_disabled",
            (
                "Chrome blocks JavaScript execution from automation. Enable "
                '"View > Developer > Allow JavaScript from Apple Events" in Google Chrome.'
            ),
        )
    if any(marker in message for marker in AUTOMATION_NOT_AUTHORIZED_MARKERS):
        return (
            "automation_not_authorized",
            "macOS Automation permission to control Google Chrome is denied for this terminal.",
        )
    return ("live_session_error", message)


def window_tab_listing_script(window_index: int) -> str:
    return rf'''
const chrome = Application("Google Chrome");
const windows = chrome.windows();
if (windows.length < {window_index}) {{
  throw new Error("Window index out of range");
}}
const targetWindow = windows[{window_index - 1}];
const tabs = targetWindow.tabs();
const payload = tabs.map((tab, idx) => ({{
  window_index: {window_index},
  tab_index: idx + 1,
  title: String(tab.title() || ""),
  url: String(tab.url() || "")
}}));
JSON.stringify(payload);
'''


def discover_chrome_tabs() -> list[ChromeTab]:
    tabs: list[ChromeTab] = []
    occurrence_counts: dict[str, int] = {}
    for window_index in range(1, get_chrome_window_count() + 1):
        tabs.extend(discover_window_tabs(window_index, occurrence_counts=occurrence_counts))
    return tabs


def discover_window_tabs(
    window_index: int,
    occurrence_counts: dict[str, int] | None = None,
    canonical_ids: dict[str, str] | None = None,
) -> list[ChromeTab]:
    ok, error = preflight_chrome_access()
    if not ok:
        raise RuntimeError(error or "Google Chrome preflight failed.")
    result = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", window_tab_listing_script(window_index)],
        capture_output=True,
        text=True,
        check=True,
    )
    discovered_at = datetime.now(UTC)
    counts = occurrence_counts if occurrence_counts is not None else {}
    canonical_by_fingerprint = canonical_ids if canonical_ids is not None else {}
    tabs: list[ChromeTab] = []
    payload = json.loads(result.stdout or "[]")
    for item in payload:
        url = str(item.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        title = (str(item.get("title") or "") or parsed.path or domain).strip()[:500]
        base_key = compute_stable_tab_base_key(url=url, title=title)
        counts[base_key] = counts.get(base_key, 0) + 1
        stable_key = f"{base_key}-{counts[base_key]}"
        duplicate_of_tab_id = canonical_by_fingerprint.get(base_key)
        if duplicate_of_tab_id is None:
            canonical_by_fingerprint[base_key] = stable_key
        tabs.append(
            ChromeTab(
                tab_id=stable_key,
                stable_key=stable_key,
                fingerprint_key=base_key,
                window_index=int(item["window_index"]),
                tab_index=int(item["tab_index"]),
                title=title,
                url=url,
                domain=domain,
                discovered_at=discovered_at,
                first_seen_at=discovered_at,
                last_seen_at=discovered_at,
                duplicate_of_tab_id=duplicate_of_tab_id,
            )
        )
    return tabs


def probe_live_javascript_support() -> tuple[bool, str | None, str | None]:
    ok, error = preflight_chrome_access()
    if not ok:
        return False, "chrome_preflight_failed", error

    script = r'''
const chrome = Application("Google Chrome");
const windows = chrome.windows();
if (windows.length < 1) {
  throw new Error("Google Chrome has no open windows");
}
const title = windows[0].activeTab().execute({javascript: "document.title"});
JSON.stringify({title: String(title || "")});
'''
    try:
        subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script],
            capture_output=True,
            text=True,
            check=True,
        )
        return True, None, None
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        reason, message = classify_live_session_error(detail)
        return False, reason, message


def compute_stable_tab_base_key(*, url: str, title: str) -> str:
    normalized_url = normalize_url_for_fingerprint(url)
    normalized_title = " ".join(title.lower().split())
    digest = hashlib.sha1(f"{normalized_url}|{normalized_title}".encode("utf-8")).hexdigest()
    return digest[:16]


def normalize_url_for_fingerprint(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=path,
        params="",
        query=parsed.query,
        fragment="",
    )
    return urlunparse(normalized)


def capture_live_tab_snapshot(
    *,
    window_index: int,
    tab_index: int,
    timeout_seconds: float = 8.0,
    attempts: int = 3,
    activation_delay_seconds: float = 0.15,
) -> dict[str, str | int | None]:
    javascript = """
(() => {
  const TEXT_LIMIT = %d;
  const FRAME_LIMIT = %d;
  const pick = (value) => typeof value === "string" ? value : "";
  const clip = (value) => value.length > TEXT_LIMIT ? value.slice(0, TEXT_LIMIT) : value;
  const collectText = (root) => {
    if (!root) {
      return "";
    }
    const inner = clip(pick(root.innerText).replace(/\\u0000/g, " ").trim());
    if (inner) {
      return inner;
    }
    return clip(pick(root.textContent).replace(/\\u0000/g, " ").trim());
  };
  const collectFrameTexts = () => {
    const texts = [];
    const frames = Array.from(document.querySelectorAll("iframe")).slice(0, FRAME_LIMIT);
    for (const frame of frames) {
      try {
        const frameDocument = frame.contentDocument;
        const frameRoot = frameDocument ? (frameDocument.body || frameDocument.documentElement) : null;
        const frameText = collectText(frameRoot);
        if (frameText) {
          texts.push(frameText);
        }
      } catch (_error) {
        // Cross-origin frames are expected and can be ignored.
      }
    }
    return texts;
  };
  const meta = document.querySelector('meta[name="description"], meta[property="og:description"]');
  const candidates = [
    document.querySelector("article"),
    document.querySelector("main"),
    document.querySelector('[role="main"]'),
    document.body,
    document.documentElement,
  ]
    .map((root) => collectText(root))
    .filter(Boolean);
  const frameTexts = collectFrameTexts();
  const text = [...candidates, ...frameTexts]
    .sort((left, right) => right.length - left.length)[0] || "";
  return JSON.stringify({
    title: pick(document.title),
    url: pick(location.href),
    excerpt: pick(meta ? meta.content : "").trim().slice(0, 280),
    text,
    text_char_count: text.length
  });
})()
""" % (LIVE_SNAPSHOT_TEXT_LIMIT, LIVE_SNAPSHOT_FRAME_LIMIT)
    javascript = javascript.strip()
    script_lines = build_live_snapshot_script_lines(
        window_index=window_index,
        tab_index=tab_index,
        timeout_seconds=timeout_seconds,
        activation_delay_seconds=activation_delay_seconds,
        javascript=javascript,
    )
    command = ["osascript"]
    for line in script_lines:
        command.extend(["-e", line])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=max(timeout_seconds + activation_delay_seconds + 2.0, 5.0),
        )
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired as exc:
        raise subprocess.CalledProcessError(
            124,
            exc.cmd,
            output=exc.output,
            stderr=f"Google Chrome got an error: {APPLE_EVENT_TIMEOUT_MARKER}. (-1712)",
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        _, message = classify_live_session_error(detail)
        raise subprocess.CalledProcessError(
            exc.returncode,
            exc.cmd,
            output=exc.output,
            stderr=message,
        ) from exc


def build_live_snapshot_script_lines(
    *,
    window_index: int,
    tab_index: int,
    timeout_seconds: float,
    activation_delay_seconds: float,
    javascript: str,
) -> list[str]:
    return [
        f"with timeout of {timeout_seconds} seconds",
        'tell application "Google Chrome"',
        f'if (count of windows) < {window_index} then error "Window index out of range"',
        f'set targetWindow to window {window_index}',
        f'if (count of tabs of targetWindow) < {tab_index} then error "Tab index out of range"',
        "set originalTabIndex to active tab index of targetWindow",
        "try",
        f"set active tab index of targetWindow to {tab_index}",
        f"delay {activation_delay_seconds}",
        f"set payload to execute active tab of targetWindow javascript {json.dumps(javascript)}",
        "set active tab index of targetWindow to originalTabIndex",
        "return payload",
        "on error errMsg number errNum",
        "set active tab index of targetWindow to originalTabIndex",
        "error errMsg number errNum",
        "end try",
        "end tell",
        "end timeout",
    ]
