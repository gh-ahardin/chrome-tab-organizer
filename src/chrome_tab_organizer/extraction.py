from __future__ import annotations

import concurrent.futures
import logging
from datetime import UTC, datetime
import subprocess
import time
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from chrome_tab_organizer.chrome import (
    capture_live_tab_snapshot,
    classify_live_session_error,
    probe_live_javascript_support,
)
from chrome_tab_organizer.config import Settings
from chrome_tab_organizer.models import ChromeTab, ExtractedContent

logger = logging.getLogger(__name__)

try:
    import trafilatura
except ModuleNotFoundError:  # pragma: no cover - depends on environment
    trafilatura = None

ACCESS_WALL_TEXT_MARKERS = (
    "sign in",
    "log in",
    "login",
    "subscribe to continue",
    "create an account",
    "join to continue",
    "access denied",
)


def _domain_allowed(domain: str, settings: Settings) -> bool:
    normalized = domain.lower()
    if settings.include_domains and normalized not in settings.include_domains:
        return False
    if settings.exclude_domains and normalized in settings.exclude_domains:
        return False
    return True


def _skip_live_session_for_domain(domain: str, settings: Settings) -> bool:
    return _domain_matches(domain, settings.live_session_skip_domains)


def _domain_matches(domain: str, candidates: list[str]) -> bool:
    normalized = domain.lower()
    for candidate in candidates:
        candidate_normalized = candidate.lower()
        if normalized == candidate_normalized or normalized.endswith(f".{candidate_normalized}"):
            return True
    return False


def _priority_live_session_domain(domain: str, settings: Settings) -> bool:
    return _domain_matches(domain, settings.live_session_priority_domains)


def _live_session_activation_delay(domain: str, settings: Settings) -> float:
    if _priority_live_session_domain(domain, settings):
        return settings.live_session_priority_activation_delay_seconds
    return settings.live_session_activation_delay_seconds


def _live_session_min_chars(domain: str, settings: Settings) -> int:
    if _priority_live_session_domain(domain, settings):
        return settings.priority_live_extract_chars
    return settings.min_live_extract_chars


def _safe_final_url(candidate_url: str | None, fallback_url: str) -> str:
    value = (candidate_url or "").strip()
    if value.startswith(("http://", "https://")):
        return value
    return fallback_url


def _should_retry_live_session_with_longer_delay(
    live_content: ExtractedContent | None,
    live_error: str | None,
    *,
    live_min_chars: int,
    activation_delay_seconds: float,
    settings: Settings,
) -> bool:
    if settings.live_session_retry_activation_delay_seconds <= activation_delay_seconds:
        return False
    if live_content is not None and live_content.text_char_count >= live_min_chars:
        return False
    normalized_error = (live_error or "").lower()
    hard_failures = (
        "timed out",
        "tab index changed",
        "window index changed",
        "javascript execution from automation",
        "automation permission",
    )
    return not any(marker in normalized_error for marker in hard_failures)


def _content_indicates_access_wall(content: ExtractedContent) -> bool:
    final_url = str(content.final_url or "").lower()
    if any(marker in final_url for marker in ("/login", "/signin", "/auth", "return_to=")):
        return True
    combined = " ".join(
        part for part in (content.title, content.excerpt, content.raw_text[:400], content.error) if part
    ).lower()
    return any(marker in combined for marker in ACCESS_WALL_TEXT_MARKERS)


def _should_attempt_live_session_after_http(
    tab: ChromeTab,
    http_content: ExtractedContent,
    settings: Settings,
) -> bool:
    if not settings.prefer_live_chrome_session or _skip_live_session_for_domain(tab.domain, settings):
        return False
    if http_content.extraction_method == "error":
        return True
    if (http_content.status_code or 0) >= 400:
        return True
    if _content_indicates_access_wall(http_content):
        return True
    return http_content.text_char_count < _live_session_min_chars(tab.domain, settings)


def _attach_live_session_metadata(
    content: ExtractedContent,
    *,
    live_attempted: bool,
    live_succeeded: bool,
    live_skipped: bool,
    live_skip_reason: str | None,
    live_error: str | None,
    live_text_char_count: int,
    live_rejected_as_too_short: bool,
) -> ExtractedContent:
    content.live_session_attempted = live_attempted
    content.live_session_succeeded = live_succeeded
    content.live_session_skipped = live_skipped
    content.live_session_skip_reason = live_skip_reason
    content.live_session_error = live_error
    content.live_session_text_char_count = live_text_char_count
    content.live_session_rejected_as_too_short = live_rejected_as_too_short
    return content


def _extract_via_http(
    tab: ChromeTab,
    settings: Settings,
    fetched_at: datetime,
    *,
    http_fallback_used: bool,
) -> ExtractedContent:
    with httpx.Client(
        follow_redirects=True,
        timeout=settings.fetch_timeout_seconds,
        headers={"User-Agent": "chrome-tab-organizer/0.1"},
    ) as client:
        response = client.get(str(tab.url))
    html = response.text

    extracted = (
        trafilatura.extract(
            html,
            include_comments=False,
            include_formatting=False,
            favor_precision=True,
        )
        if trafilatura is not None
        else None
    )
    title = tab.title
    excerpt: str | None = None
    method = "trafilatura"
    raw_text = (extracted or "").strip()

    soup: BeautifulSoup | None = None
    if not raw_text:
        soup = BeautifulSoup(html, "html.parser")
        title = (soup.title.string if soup.title and soup.title.string else title).strip()
        paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        raw_text = "\n\n".join(text for text in paragraphs if text)
        excerpt = next((text[:280] for text in paragraphs if text), None)
        method = "beautifulsoup_paragraphs"

    if not raw_text:
        if soup is None:
            soup = BeautifulSoup(html, "html.parser")
        body_text = soup.get_text(" ", strip=True)
        raw_text = body_text[:4000]
        excerpt = raw_text[:280] if raw_text else None
        method = "beautifulsoup_body"

    return ExtractedContent(
        tab_id=tab.tab_id,
        final_url=_safe_final_url(str(response.url), str(tab.url)),
        status_code=response.status_code,
        content_type=response.headers.get("content-type"),
        title=title[:500],
        excerpt=excerpt,
        raw_text=raw_text,
        text_char_count=len(raw_text),
        extraction_method=method,
        http_fallback_used=http_fallback_used,
        fetched_at=fetched_at,
        error=(
            f"Non-200 status during HTTP fallback: {response.status_code}"
            if http_fallback_used and response.status_code >= 400
            else None
        ),
    )


def _attempt_live_session(
    tab: ChromeTab,
    settings: Settings,
    fetched_at: datetime,
    *,
    live_min_chars: int,
    activation_delay_seconds: float,
) -> tuple[ExtractedContent | None, str | None, int, bool]:
    live_content, live_error = extract_from_live_session(
        tab,
        settings,
        fetched_at,
        activation_delay_seconds=activation_delay_seconds,
    )
    if _should_retry_live_session_with_longer_delay(
        live_content,
        live_error,
        live_min_chars=live_min_chars,
        activation_delay_seconds=activation_delay_seconds,
        settings=settings,
    ):
        retry_content, retry_error = extract_from_live_session(
            tab,
            settings,
            fetched_at,
            activation_delay_seconds=settings.live_session_retry_activation_delay_seconds,
        )
        if retry_content and (live_content is None or retry_content.text_char_count >= live_content.text_char_count):
            live_content = retry_content
            live_error = retry_error
    live_text_char_count = live_content.text_char_count if live_content else 0
    live_rejected_as_too_short = bool(
        live_content is not None and live_content.text_char_count < live_min_chars
    )
    return live_content, live_error, live_text_char_count, live_rejected_as_too_short


def extract_tabs(tabs: list[ChromeTab], settings: Settings) -> list[ExtractedContent]:
    """Extract content for a list of tabs.

    Tabs in ``settings.live_session_domains`` are extracted serially via live
    Chrome session (AppleScript) and run first. All other tabs are extracted
    concurrently via HTTP. This avoids waking Chrome tabs unnecessarily.
    """
    effective_live_domains = settings.live_session_domains if settings.prefer_live_chrome_session else []

    live_tabs = [t for t in tabs if _domain_matches(t.domain, effective_live_domains)]
    http_tabs = [t for t in tabs if not _domain_matches(t.domain, effective_live_domains)]

    # Probe once if any live-session tabs exist
    live_session_available = False
    if live_tabs:
        live_session_available, unavailable_reason, unavailable_message = probe_live_javascript_support()
        if not live_session_available:
            if settings.require_live_chrome_session:
                raise RuntimeError(unavailable_message or "Live Chrome session extraction is unavailable.")
            logger.warning(
                "Live Chrome session unavailable; falling back to HTTP for all tabs: %s",
                unavailable_message or unavailable_reason,
            )

    # HTTP tabs: fully concurrent
    http_results: list[ExtractedContent] = []
    if http_tabs:
        with concurrent.futures.ThreadPoolExecutor(max_workers=settings.max_concurrency) as executor:
            futures = [
                executor.submit(extract_single_tab, tab, settings, live_session_available=False)
                for tab in http_tabs
            ]
            http_results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # Live session tabs: serial to avoid Chrome memory pressure
    live_results: list[ExtractedContent] = []
    for index, tab in enumerate(live_tabs):
        if index > 0 and settings.live_extract_tab_pause_seconds > 0:
            time.sleep(settings.live_extract_tab_pause_seconds)
        live_results.append(
            extract_single_tab(tab, settings, live_session_available=live_session_available)
        )

    return http_results + live_results


def extract_single_tab(
    tab: ChromeTab,
    settings: Settings,
    *,
    live_session_available: bool = False,
) -> ExtractedContent:
    """Extract content for a single tab.

    If ``live_session_available`` is True and the tab's domain is in
    ``settings.live_session_domains``, live Chrome session extraction is
    attempted first with HTTP as a fallback. Otherwise HTTP only.
    """
    fetched_at = datetime.now(UTC)

    if not _domain_allowed(tab.domain, settings):
        return ExtractedContent(
            tab_id=tab.tab_id,
            title=tab.title,
            raw_text="",
            text_char_count=0,
            extraction_method="skipped_by_domain_filter",
            live_session_skipped=True,
            live_session_skip_reason="domain_filter",
            fetched_at=fetched_at,
            error="Skipped by domain filter.",
        )

    use_live = (
        live_session_available
        and _domain_matches(tab.domain, settings.live_session_domains)
        and not _skip_live_session_for_domain(tab.domain, settings)
    )

    try:
        if use_live:
            activation_delay = _live_session_activation_delay(tab.domain, settings)
            live_min_chars = _live_session_min_chars(tab.domain, settings)
            live_content, live_error, live_char_count, live_too_short = _attempt_live_session(
                tab, settings, fetched_at,
                live_min_chars=live_min_chars,
                activation_delay_seconds=activation_delay,
            )
            if live_content and live_content.text_char_count >= live_min_chars:
                logger.info("Live session succeeded for %s (%s chars)", tab.tab_id, live_char_count)
                live_content.live_session_attempted = True
                live_content.live_session_succeeded = True
                live_content.live_session_text_char_count = live_char_count
                return live_content
            if live_content:
                logger.info(
                    "Live session too short for %s (%s chars), falling back to HTTP",
                    tab.tab_id, live_char_count,
                )
            else:
                logger.info("Live session failed for %s: %s", tab.tab_id, live_error)
            http_content = _extract_via_http(tab, settings, fetched_at, http_fallback_used=True)
            return _attach_live_session_metadata(
                http_content,
                live_attempted=True,
                live_succeeded=bool(live_content),
                live_skipped=False,
                live_skip_reason=None,
                live_error=live_error,
                live_text_char_count=live_char_count,
                live_rejected_as_too_short=live_too_short,
            )

        return _extract_via_http(tab, settings, fetched_at, http_fallback_used=False)

    except Exception as exc:  # noqa: BLE001
        logger.warning("Extraction failed for %s (%s): %s", tab.tab_id, tab.domain, exc)
        return ExtractedContent(
            tab_id=tab.tab_id,
            title=tab.title,
            raw_text="",
            text_char_count=0,
            extraction_method="error",
            http_fallback_used=False,
            fetched_at=fetched_at,
            error=str(exc),
        )


def canonical_domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def extract_from_live_session(
    tab: ChromeTab,
    settings: Settings,
    fetched_at: datetime,
    *,
    activation_delay_seconds: float,
) -> tuple[ExtractedContent | None, str | None]:
    try:
        snapshot = _capture_live_tab_snapshot_with_retry(tab, settings, activation_delay_seconds)
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)

    raw_text = (str(snapshot.get("text") or "")).strip()
    if not raw_text:
        return None, "Live session returned no text."

    final_url = _safe_final_url(str(snapshot.get("url") or ""), str(tab.url))
    return (
        ExtractedContent(
        tab_id=tab.tab_id,
        final_url=final_url,
        status_code=200,
        content_type="text/html; source=chrome-session",
        title=str(snapshot.get("title") or tab.title)[:500],
        excerpt=(str(snapshot.get("excerpt") or "")[:280] or None),
        raw_text=raw_text,
        text_char_count=int(snapshot.get("text_char_count") or len(raw_text)),
        extraction_method="chrome_live_dom",
        live_session_attempted=True,
        live_session_succeeded=True,
        live_session_text_char_count=int(snapshot.get("text_char_count") or len(raw_text)),
        fetched_at=fetched_at,
        ),
        None,
    )

def _capture_live_tab_snapshot_with_retry(
    tab: ChromeTab,
    settings: Settings,
    activation_delay_seconds: float,
) -> dict[str, str | int | None]:
    max_attempts = max(1, settings.session_extract_attempts)
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return capture_live_tab_snapshot(
                window_index=tab.window_index,
                tab_index=tab.tab_index,
                timeout_seconds=settings.session_extract_timeout_seconds,
                attempts=1,
                activation_delay_seconds=activation_delay_seconds,
            )
        except subprocess.CalledProcessError as exc:
            reason, message = classify_live_session_error(exc.stderr or exc.stdout or str(exc))
            if reason in {"appleevent_timed_out", "tab_index_out_of_range", "window_index_out_of_range"}:
                raise RuntimeError(message) from exc
            last_error = RuntimeError(message)
            if attempt < max_attempts:
                time.sleep(1)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < max_attempts:
                time.sleep(1)
    if last_error is not None:
        raise last_error
    raise RuntimeError("Live tab snapshot failed without a captured error.")
