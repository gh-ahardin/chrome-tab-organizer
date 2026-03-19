import subprocess
from datetime import UTC, datetime

from chrome_tab_organizer.config import Settings
from chrome_tab_organizer.extraction import (
    _capture_live_tab_snapshot_with_retry,
    _content_indicates_access_wall,
    _domain_allowed,
    _should_retry_live_session_with_longer_delay,
    _skip_live_session_for_domain,
    extract_from_live_session,
    extract_single_tab,
    extract_tabs,
)
from chrome_tab_organizer.models import ChromeTab, ExtractedContent


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
    assert content.http_fallback_used is False


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


def test_priority_live_session_domain_accepts_shorter_authenticated_content(monkeypatch) -> None:
    now = datetime.now(UTC)
    tab = ChromeTab(
        tab_id="linkedin-1",
        stable_key="linkedin-1",
        fingerprint_key="linkedin-1",
        window_index=1,
        tab_index=1,
        title="LinkedIn post",
        url="https://www.linkedin.com/posts/example",
        domain="www.linkedin.com",
        discovered_at=now,
        first_seen_at=now,
        last_seen_at=now,
    )
    settings = Settings(
        prefer_live_chrome_session=True,
        min_live_extract_chars=200,
        priority_live_extract_chars=80,
    )

    monkeypatch.setattr(
        "chrome_tab_organizer.extraction.extract_from_live_session",
        lambda *args, **kwargs: (
            ExtractedContent(
                tab_id=tab.tab_id,
                final_url=tab.url,
                status_code=200,
                content_type="text/html; source=chrome-session",
                title=tab.title,
                raw_text="L" * 120,
                text_char_count=120,
                extraction_method="chrome_live_dom",
                fetched_at=now,
            ),
            None,
        ),
    )

    content = extract_single_tab(tab, settings, live_session_available=True)
    assert content.extraction_method == "chrome_live_dom"
    assert content.http_fallback_used is False
    assert content.live_session_succeeded is True


def test_extract_single_tab_retries_live_session_with_longer_delay(monkeypatch) -> None:
    now = datetime.now(UTC)
    tab = ChromeTab(
        tab_id="tab-retry",
        stable_key="tab-retry",
        fingerprint_key="tab-retry",
        window_index=1,
        tab_index=1,
        title="Example retry",
        url="https://example.com/retry",
        domain="example.com",
        discovered_at=now,
        first_seen_at=now,
        last_seen_at=now,
    )
    settings = Settings(
        prefer_live_chrome_session=True,
        min_live_extract_chars=200,
        live_session_activation_delay_seconds=0.2,
        live_session_retry_activation_delay_seconds=1.2,
    )

    attempts: list[float] = []

    def fake_extract_from_live_session(tab, settings, fetched_at, *, activation_delay_seconds):
        attempts.append(activation_delay_seconds)
        if activation_delay_seconds < 1:
            return None, "Live session returned no text."
        return (
            ExtractedContent(
                tab_id=tab.tab_id,
                final_url=tab.url,
                status_code=200,
                content_type="text/html; source=chrome-session",
                title=tab.title,
                raw_text="R" * 300,
                text_char_count=300,
                extraction_method="chrome_live_dom",
                fetched_at=fetched_at,
            ),
            None,
        )

    monkeypatch.setattr("chrome_tab_organizer.extraction.extract_from_live_session", fake_extract_from_live_session)
    monkeypatch.setattr(
        "chrome_tab_organizer.extraction._extract_via_http",
        lambda *args, **kwargs: ExtractedContent(
            tab_id=tab.tab_id,
            final_url=tab.url,
            status_code=403,
            content_type="text/html",
            title=tab.title,
            raw_text="",
            text_char_count=0,
            extraction_method="trafilatura",
            fetched_at=now,
            error="Non-200 status during HTTP fallback: 403",
        ),
    )

    content = extract_single_tab(tab, settings, live_session_available=True)
    assert attempts == [0.2, 1.2]
    assert content.extraction_method == "chrome_live_dom"
    assert content.live_session_succeeded is True


def test_extract_from_live_session_falls_back_from_chrome_error_url(monkeypatch) -> None:
    now = datetime.now(UTC)
    tab = ChromeTab(
        tab_id="tab-chrome-error",
        stable_key="tab-chrome-error",
        fingerprint_key="tab-chrome-error",
        window_index=1,
        tab_index=1,
        title="Example",
        url="https://example.com/original",
        domain="example.com",
        discovered_at=now,
        first_seen_at=now,
        last_seen_at=now,
    )
    settings = Settings(prefer_live_chrome_session=True)

    monkeypatch.setattr(
        "chrome_tab_organizer.extraction._capture_live_tab_snapshot_with_retry",
        lambda *args, **kwargs: {
            "title": "Error page",
            "url": "chrome-error://chromewebdata/",
            "text": "Recovered text",
            "text_char_count": 14,
        },
    )

    content, error = extract_from_live_session(
        tab,
        settings,
        now,
        activation_delay_seconds=0.2,
    )
    assert error is None
    assert content is not None
    assert str(content.final_url) == "https://example.com/original"


def test_extract_single_tab_prefers_http_for_public_pages(monkeypatch) -> None:
    now = datetime.now(UTC)
    tab = ChromeTab(
        tab_id="tab-http-first",
        stable_key="tab-http-first",
        fingerprint_key="tab-http-first",
        window_index=1,
        tab_index=1,
        title="Example",
        url="https://example.com/public",
        domain="example.com",
        discovered_at=now,
        first_seen_at=now,
        last_seen_at=now,
    )
    settings = Settings(prefer_live_chrome_session=True, min_live_extract_chars=200)
    live_calls = {"count": 0}

    monkeypatch.setattr(
        "chrome_tab_organizer.extraction._extract_via_http",
        lambda *args, **kwargs: ExtractedContent(
            tab_id=tab.tab_id,
            final_url=tab.url,
            status_code=200,
            content_type="text/html",
            title=tab.title,
            raw_text="H" * 500,
            text_char_count=500,
            extraction_method="trafilatura",
            fetched_at=now,
        ),
    )

    def fake_extract_from_live_session(*args, **kwargs):
        live_calls["count"] += 1
        return None, "should not be called"

    monkeypatch.setattr("chrome_tab_organizer.extraction.extract_from_live_session", fake_extract_from_live_session)

    content = extract_single_tab(tab, settings, live_session_available=True)
    assert live_calls["count"] == 0
    assert content.extraction_method == "trafilatura"
    assert content.live_session_skipped is True
    assert content.live_session_skip_reason == "http_content_sufficient"


def test_capture_live_tab_snapshot_fails_fast_for_tab_index_change(monkeypatch) -> None:
    now = datetime.now(UTC)
    tab = ChromeTab(
        tab_id="tab-moving",
        stable_key="tab-moving",
        fingerprint_key="tab-moving",
        window_index=1,
        tab_index=1,
        title="Example",
        url="https://example.com/original",
        domain="example.com",
        discovered_at=now,
        first_seen_at=now,
        last_seen_at=now,
    )
    settings = Settings(session_extract_attempts=3)
    calls = {"count": 0}

    def raise_tab_change(*args, **kwargs):
        calls["count"] += 1
        raise subprocess.CalledProcessError(1, ["osascript"], output="", stderr="Tab index out of range")

    monkeypatch.setattr("chrome_tab_organizer.extraction.capture_live_tab_snapshot", raise_tab_change)

    try:
        _capture_live_tab_snapshot_with_retry(tab, settings, activation_delay_seconds=0.2)
    except RuntimeError as exc:
        assert "tab index changed" in str(exc).lower()
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected RuntimeError for tab index change")

    assert calls["count"] == 1


def test_capture_live_tab_snapshot_fails_fast_for_appleevent_timeout(monkeypatch) -> None:
    now = datetime.now(UTC)
    tab = ChromeTab(
        tab_id="tab-timeout",
        stable_key="tab-timeout",
        fingerprint_key="tab-timeout",
        window_index=1,
        tab_index=1,
        title="Example",
        url="https://example.com/original",
        domain="example.com",
        discovered_at=now,
        first_seen_at=now,
        last_seen_at=now,
    )
    settings = Settings(session_extract_attempts=3)
    calls = {"count": 0}

    def raise_timeout(*args, **kwargs):
        calls["count"] += 1
        raise subprocess.CalledProcessError(
            1,
            ["osascript"],
            output="",
            stderr="Google Chrome got an error: AppleEvent timed out. (-1712)",
        )

    monkeypatch.setattr("chrome_tab_organizer.extraction.capture_live_tab_snapshot", raise_timeout)

    try:
        _capture_live_tab_snapshot_with_retry(tab, settings, activation_delay_seconds=0.2)
    except RuntimeError as exc:
        assert "timed out" in str(exc).lower()
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected RuntimeError for AppleEvent timeout")

    assert calls["count"] == 1


def test_should_not_retry_live_session_with_longer_delay_after_timeout() -> None:
    settings = Settings(
        live_session_activation_delay_seconds=0.2,
        live_session_retry_activation_delay_seconds=1.2,
    )

    should_retry = _should_retry_live_session_with_longer_delay(
        None,
        "Google Chrome automation timed out during live extraction.",
        live_min_chars=200,
        activation_delay_seconds=0.2,
        settings=settings,
    )

    assert should_retry is False


# --- _domain_allowed ---

def test_domain_allowed_no_filters() -> None:
    settings = Settings()
    assert _domain_allowed("example.com", settings) is True


def test_domain_allowed_include_list_passes_listed_domain() -> None:
    settings = Settings(include_domains=["example.com", "github.com"])
    assert _domain_allowed("example.com", settings) is True


def test_domain_allowed_include_list_blocks_unlisted_domain() -> None:
    settings = Settings(include_domains=["example.com"])
    assert _domain_allowed("other.com", settings) is False


def test_domain_allowed_exclude_list_blocks_listed_domain() -> None:
    settings = Settings(exclude_domains=["spam.com"])
    assert _domain_allowed("spam.com", settings) is False
    assert _domain_allowed("example.com", settings) is True


def test_domain_allowed_exclude_beats_include_when_both_match() -> None:
    settings = Settings(include_domains=["spam.com"], exclude_domains=["spam.com"])
    assert _domain_allowed("spam.com", settings) is False


# --- _content_indicates_access_wall ---

def _make_content(
    *,
    raw_text: str = "",
    final_url: str = "https://example.com/page",
    title: str = "Test",
    excerpt: str | None = None,
) -> ExtractedContent:
    return ExtractedContent(
        tab_id="t",
        final_url=final_url,
        status_code=200,
        content_type="text/html",
        title=title,
        excerpt=excerpt,
        raw_text=raw_text,
        text_char_count=len(raw_text),
        extraction_method="test",
        fetched_at=datetime.now(UTC),
    )


def test_access_wall_detected_via_login_in_url() -> None:
    content = _make_content(final_url="https://example.com/login?return_to=/page")
    assert _content_indicates_access_wall(content) is True


def test_access_wall_detected_via_signin_in_url() -> None:
    content = _make_content(final_url="https://example.com/signin")
    assert _content_indicates_access_wall(content) is True


def test_access_wall_detected_via_sign_in_in_text() -> None:
    content = _make_content(raw_text="Please sign in to continue reading this article.")
    assert _content_indicates_access_wall(content) is True


def test_access_wall_detected_via_log_in_in_text() -> None:
    content = _make_content(raw_text="You must log in to access this content.")
    assert _content_indicates_access_wall(content) is True


def test_access_wall_detected_via_subscribe_to_continue() -> None:
    content = _make_content(raw_text="Subscribe to continue reading premium content.")
    assert _content_indicates_access_wall(content) is True


def test_access_wall_not_triggered_for_normal_content() -> None:
    content = _make_content(raw_text="This is a great article about machine learning techniques.")
    assert _content_indicates_access_wall(content) is False


# --- _skip_live_session_for_domain ---

def test_skip_live_session_for_youtube() -> None:
    settings = Settings()
    assert _skip_live_session_for_domain("youtube.com", settings) is True


def test_skip_live_session_for_www_youtube_subdomain() -> None:
    settings = Settings()
    assert _skip_live_session_for_domain("www.youtube.com", settings) is True


def test_skip_live_session_not_triggered_for_unlisted_domain() -> None:
    settings = Settings()
    assert _skip_live_session_for_domain("github.com", settings) is False


def test_skip_live_session_respects_custom_skip_list() -> None:
    settings = Settings(live_session_skip_domains=["badsite.com"])
    assert _skip_live_session_for_domain("badsite.com", settings) is True
    assert _skip_live_session_for_domain("youtube.com", settings) is False
