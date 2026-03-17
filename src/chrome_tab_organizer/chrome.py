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


def get_chrome_window_count() -> int:
    result = subprocess.run(
        ["osascript", "-e", WINDOW_COUNT_SCRIPT],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(result.stdout.strip() or "0")


def window_tab_listing_script(window_index: int) -> str:
    return rf'''
set output to "["
tell application "Google Chrome"
    if (count of windows) < {window_index} then error "Window index out of range"
    set tabCount to count of tabs of window {window_index}
    repeat with t from 1 to tabCount
        set tabTitle to title of tab t of window {window_index}
        set tabURL to URL of tab t of window {window_index}
        set jsonItem to "{{""window_index"":{window_index},""tab_index"":" & t & ",""title"":" & my json_quote(tabTitle) & ",""url"":" & my json_quote(tabURL) & "}}"
        if output is not "[" then
            set output to output & ","
        end if
        set output to output & jsonItem
    end repeat
end tell
set output to output & "]"
return output

on json_quote(inputText)
    if inputText is missing value then
        return "\"\""
    end if
    set escapedText to inputText
    set escapedText to my replace_text("\\", "\\\\", escapedText)
    set escapedText to my replace_text("\"", "\\\"", escapedText)
    set escapedText to my replace_text(return, "\\n", escapedText)
    set escapedText to my replace_text(linefeed, "\\n", escapedText)
    set escapedText to my replace_text(tab, "\\t", escapedText)
    return "\"" & escapedText & "\""
end json_quote

on replace_text(search_string, replacement_string, source_text)
    set AppleScript's text item delimiters to search_string
    set text_items to every text item of source_text
    set AppleScript's text item delimiters to replacement_string
    set source_text to text_items as string
    set AppleScript's text item delimiters to ""
    return source_text
end replace_text
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
) -> list[ChromeTab]:
    result = subprocess.run(
        ["osascript", "-e", window_tab_listing_script(window_index)],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    discovered_at = datetime.now(UTC)
    counts = occurrence_counts if occurrence_counts is not None else {}
    tabs: list[ChromeTab] = []
    for item in payload:
        url = item.get("url", "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        title = (item.get("title") or parsed.path or domain).strip()[:500]
        base_key = compute_stable_tab_base_key(url=url, title=title)
        counts[base_key] = counts.get(base_key, 0) + 1
        stable_key = f"{base_key}-{counts[base_key]}"
        tabs.append(
            ChromeTab(
                tab_id=stable_key,
                stable_key=stable_key,
                window_index=item["window_index"],
                tab_index=item["tab_index"],
                title=title,
                url=url,
                domain=domain,
                discovered_at=discovered_at,
                first_seen_at=discovered_at,
                last_seen_at=discovered_at,
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
