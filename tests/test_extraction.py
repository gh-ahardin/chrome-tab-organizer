from datetime import UTC, datetime

from chrome_tab_organizer.config import Settings
from chrome_tab_organizer.extraction import extract_tabs
from chrome_tab_organizer.models import ChromeTab


def _sample_tab() -> ChromeTab:
    now = datetime.now(UTC)
    return ChromeTab(
        tab_id="tab-1",
        stable_key="tab-1",
        fingerprint_key="fingerprint-1",
        window_index=1,
        tab_index=1,
        title="Example",
        url="https://example.com/page",
        domain="example.com",
        discovered_at=now,
        first_seen_at=now,
        last_seen_at=now,
    )


def test_extract_tabs_skips_live_session_when_probe_fails(monkeypatch) -> None:
    settings = Settings(
        prefer_live_chrome_session=True,
        require_live_chrome_session=False,
    )
    tab = _sample_tab()

    monkeypatch.setattr(
        "chrome_tab_organizer.extraction.probe_live_javascript_support",
        lambda: (
            False,
            "javascript_from_apple_events_disabled",
            "Chrome blocks JavaScript execution from automation.",
        ),
    )

    class DummyResponse:
        status_code = 200
        text = "<html><head><title>Example</title></head><body><p>Hello world.</p></body></html>"
        headers = {"content-type": "text/html"}
        url = "https://example.com/page"

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, url: str) -> DummyResponse:
            return DummyResponse()

    monkeypatch.setattr("chrome_tab_organizer.extraction.httpx.Client", DummyClient)

    contents = extract_tabs([tab], settings)
    assert len(contents) == 1
    content = contents[0]
    assert content.live_session_attempted is False
    assert content.live_session_skipped is True
    assert content.live_session_skip_reason == "javascript_from_apple_events_disabled"
    assert content.http_fallback_used is True


def test_extract_tabs_can_fail_fast_when_live_session_required(monkeypatch) -> None:
    settings = Settings(
        prefer_live_chrome_session=True,
        require_live_chrome_session=True,
    )
    tab = _sample_tab()

    monkeypatch.setattr(
        "chrome_tab_organizer.extraction.probe_live_javascript_support",
        lambda: (
            False,
            "javascript_from_apple_events_disabled",
            "Chrome blocks JavaScript execution from automation.",
        ),
    )

    try:
        extract_tabs([tab], settings)
    except RuntimeError as exc:
        assert "Chrome blocks JavaScript execution from automation." in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected RuntimeError when live session is required.")
