import subprocess

from chrome_tab_organizer.chrome import (
    classify_live_session_error,
    preflight_chrome_access,
    probe_live_javascript_support,
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
