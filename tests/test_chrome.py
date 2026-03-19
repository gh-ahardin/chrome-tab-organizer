import subprocess

from chrome_tab_organizer.chrome import (
    LIVE_SNAPSHOT_FRAME_LIMIT,
    LIVE_SNAPSHOT_TEXT_LIMIT,
    build_live_snapshot_script_lines,
    classify_live_session_error,
    preflight_chrome_access,
    probe_live_javascript_support,
    capture_live_tab_snapshot,
    window_tab_listing_script,
)


def test_window_tab_listing_script_uses_tab_delimited_output() -> None:
    script = window_tab_listing_script(1)
    assert 'const chrome = Application("Google Chrome");' in script
    assert "JSON.stringify(payload);" in script


def test_preflight_reports_not_running(monkeypatch) -> None:
    monkeypatch.setattr("chrome_tab_organizer.chrome.is_chrome_running", lambda: False)
    ok, error = preflight_chrome_access()
    assert ok is False
    assert error == "Google Chrome is not running."


def test_preflight_reports_applescript_error(monkeypatch) -> None:
    def raise_error() -> bool:
        raise subprocess.CalledProcessError(
            1,
            ["osascript"],
            output="",
            stderr="Not authorized to send Apple events to Google Chrome.",
        )

    monkeypatch.setattr("chrome_tab_organizer.chrome.is_chrome_running", raise_error)
    ok, error = preflight_chrome_access()
    assert ok is False
    assert "Not authorized" in (error or "")


def test_classify_live_session_error_for_disabled_js() -> None:
    reason, message = classify_live_session_error(
        "Error: Executing JavaScript through AppleScript is turned off."
    )
    assert reason == "javascript_from_apple_events_disabled"
    assert "Allow JavaScript from Apple Events" in message


def test_classify_live_session_error_for_tab_index_change() -> None:
    reason, message = classify_live_session_error("Tab index out of range")
    assert reason == "tab_index_out_of_range"
    assert "tab index changed" in message.lower()


def test_classify_live_session_error_for_appleevent_timeout() -> None:
    reason, message = classify_live_session_error("Google Chrome got an error: AppleEvent timed out. (-1712)")
    assert reason == "appleevent_timed_out"
    assert "timed out" in message.lower()


def test_probe_live_javascript_support_reports_disabled_js(monkeypatch) -> None:
    monkeypatch.setattr("chrome_tab_organizer.chrome.preflight_chrome_access", lambda: (True, None))

    def raise_error(*args, **kwargs):
        raise subprocess.CalledProcessError(
            1,
            ["osascript"],
            output="",
            stderr="Executing JavaScript through AppleScript is turned off.",
        )

    monkeypatch.setattr("chrome_tab_organizer.chrome.subprocess.run", raise_error)
    ok, reason, message = probe_live_javascript_support()
    assert ok is False
    assert reason == "javascript_from_apple_events_disabled"
    assert "Allow JavaScript from Apple Events" in (message or "")


def test_live_snapshot_script_does_not_reorder_windows() -> None:
    script_lines = build_live_snapshot_script_lines(
        window_index=2,
        tab_index=3,
        timeout_seconds=8.0,
        activation_delay_seconds=0.2,
        javascript="document.title",
    )
    assert not any("set index of targetWindow to 1" in line for line in script_lines)


def test_capture_live_tab_snapshot_limits_payload_before_return(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["timeout"] = kwargs.get("timeout")

        class Result:
            stdout = '{"title":"Example","url":"https://example.com","text":"hello","text_char_count":5}'

        return Result()

    monkeypatch.setattr("chrome_tab_organizer.chrome.subprocess.run", fake_run)

    snapshot = capture_live_tab_snapshot(window_index=1, tab_index=1)
    assert snapshot["text"] == "hello"

    command = captured["command"]
    assert any(f"const TEXT_LIMIT = {LIVE_SNAPSHOT_TEXT_LIMIT};" in part for part in command)
    assert any(f"const FRAME_LIMIT = {LIVE_SNAPSHOT_FRAME_LIMIT};" in part for part in command)
    assert any("slice(0, FRAME_LIMIT)" in part for part in command)
    assert captured["timeout"] >= 10
