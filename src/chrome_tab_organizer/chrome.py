from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from urllib.parse import urlparse, urlunparse

from chrome_tab_organizer.models import ChromeTab

WINDOW_COUNT_SCRIPT = r'''
tell application "Google Chrome"
    return count of windows
end tell
'''

CHROME_RUNNING_SCRIPT = r'''
application "Google Chrome" is running
'''


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
) -> dict[str, str | int | None]:
    javascript = """
(() => {
  const pick = (value) => typeof value === "string" ? value : "";
  const meta = document.querySelector('meta[name="description"], meta[property="og:description"]');
  const article = document.querySelector("article");
  const root = article || document.body || document.documentElement;
  const text = pick(root ? root.innerText : "").replace(/\\u0000/g, " ").trim();
  return JSON.stringify({
    title: pick(document.title),
    url: pick(location.href),
    excerpt: pick(meta ? meta.content : "").trim().slice(0, 280),
    text: text.slice(0, 50000),
    text_char_count: text.length
  });
})()
""".strip()
    apple_script = f'''
with timeout of {timeout_seconds} seconds
    tell application "Google Chrome"
        if (count of windows) < {window_index} then error "Window index out of range"
        set targetWindow to window {window_index}
        if (count of tabs of targetWindow) < {tab_index} then error "Tab index out of range"
        set originalTabIndex to active tab index of targetWindow
        set index of targetWindow to 1
        set active tab index of targetWindow to {tab_index}
        delay 0.15
        set payload to execute active tab of targetWindow javascript {json.dumps(javascript)}
        set active tab index of targetWindow to originalTabIndex
        return payload
    end tell
end timeout
'''
    last_error: subprocess.CalledProcessError | None = None
    for _ in range(max(1, attempts)):
        try:
            result = subprocess.run(
                ["osascript", "-e", apple_script],
                capture_output=True,
                text=True,
                check=True,
            )
            return json.loads(result.stdout)
        except subprocess.CalledProcessError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError("Live tab snapshot failed without a captured subprocess error.")
