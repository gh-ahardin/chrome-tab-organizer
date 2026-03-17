from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from urllib.parse import urlparse

from chrome_tab_organizer.models import ChromeTab

APPLE_SCRIPT = r'''
set output to "["
tell application "Google Chrome"
    set windowCount to count of windows
    repeat with w from 1 to windowCount
        set tabCount to count of tabs of window w
        repeat with t from 1 to tabCount
            set tabTitle to title of tab t of window w
            set tabURL to URL of tab t of window w
            set jsonItem to "{\"window_index\":" & w & ",\"tab_index\":" & t & ",\"title\":" & my json_quote(tabTitle) & ",\"url\":" & my json_quote(tabURL) & "}"
            if output is not "[" then
                set output to output & ","
            end if
            set output to output & jsonItem
        end repeat
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
    result = subprocess.run(
        ["osascript", "-e", APPLE_SCRIPT],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    discovered_at = datetime.now(UTC)
    tabs: list[ChromeTab] = []
    for item in payload:
        url = item.get("url", "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        tab_id = f"w{item['window_index']}-t{item['tab_index']}"
        tabs.append(
            ChromeTab(
                tab_id=tab_id,
                window_index=item["window_index"],
                tab_index=item["tab_index"],
                title=(item.get("title") or parsed.path or domain).strip()[:500],
                url=url,
                domain=domain,
                discovered_at=discovered_at,
            )
        )
    return tabs


def capture_live_tab_snapshot(
    *,
    window_index: int,
    tab_index: int,
    timeout_seconds: float = 8.0,
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
    result = subprocess.run(
        ["osascript", "-e", apple_script],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)
