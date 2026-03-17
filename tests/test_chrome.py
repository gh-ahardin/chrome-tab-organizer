import subprocess

from chrome_tab_organizer.chrome import preflight_chrome_access, window_tab_listing_script


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
