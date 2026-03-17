from __future__ import annotations

import concurrent.futures
import logging
from datetime import UTC, datetime
import time
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from chrome_tab_organizer.chrome import capture_live_tab_snapshot
from chrome_tab_organizer.config import Settings
from chrome_tab_organizer.models import ChromeTab, ExtractedContent

logger = logging.getLogger(__name__)

try:
    import trafilatura
except ModuleNotFoundError:  # pragma: no cover - depends on environment
    trafilatura = None


def _domain_allowed(domain: str, settings: Settings) -> bool:
    normalized = domain.lower()
    if settings.include_domains and normalized not in settings.include_domains:
        return False
    if settings.exclude_domains and normalized in settings.exclude_domains:
        return False
    return True


def _skip_live_session_for_domain(domain: str, settings: Settings) -> bool:
    return domain.lower() in settings.live_session_skip_domains


def extract_tabs(tabs: list[ChromeTab], settings: Settings) -> list[ExtractedContent]:
    if settings.prefer_live_chrome_session:
        contents: list[ExtractedContent] = []
        for index, tab in enumerate(tabs):
            if index > 0 and settings.live_extract_tab_pause_seconds > 0:
                time.sleep(settings.live_extract_tab_pause_seconds)
            contents.append(extract_single_tab(tab, settings))
        return contents
    with concurrent.futures.ThreadPoolExecutor(max_workers=settings.max_concurrency) as executor:
        futures = [executor.submit(extract_single_tab, tab, settings) for tab in tabs]
        return [future.result() for future in concurrent.futures.as_completed(futures)]


def extract_single_tab(tab: ChromeTab, settings: Settings) -> ExtractedContent:
    fetched_at = datetime.now(UTC)
    if not _domain_allowed(tab.domain, settings):
        return ExtractedContent(
            tab_id=tab.tab_id,
            title=tab.title,
            raw_text="",
            text_char_count=0,
            extraction_method="skipped_by_domain_filter",
            fetched_at=fetched_at,
            error="Skipped by domain filter.",
        )

    try:
        if settings.prefer_live_chrome_session and not _skip_live_session_for_domain(tab.domain, settings):
            live_content = extract_from_live_session(tab, settings, fetched_at)
            if live_content and live_content.text_char_count >= settings.min_live_extract_chars:
                return live_content

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

        if not raw_text:
            soup = BeautifulSoup(html, "html.parser")
            title = (soup.title.string if soup.title and soup.title.string else title).strip()
            paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
            raw_text = "\n\n".join(text for text in paragraphs if text)
            excerpt = next((text[:280] for text in paragraphs if text), None)
            method = "beautifulsoup_paragraphs"

        if not raw_text:
            soup = BeautifulSoup(html, "html.parser")
            body_text = soup.get_text(" ", strip=True)
            raw_text = body_text[:4000]
            excerpt = raw_text[:280] if raw_text else None
            method = "beautifulsoup_body"

        return ExtractedContent(
            tab_id=tab.tab_id,
            final_url=str(response.url),
            status_code=response.status_code,
            content_type=response.headers.get("content-type"),
            title=title[:500],
            excerpt=excerpt,
            raw_text=raw_text,
            text_char_count=len(raw_text),
            extraction_method=method,
            fetched_at=fetched_at,
            error=(
                f"Non-200 status during HTTP fallback: {response.status_code}"
                if response.status_code >= 400
                else None
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Extraction failed for %s (%s): %s", tab.tab_id, tab.domain, exc)
        return ExtractedContent(
            tab_id=tab.tab_id,
            title=tab.title,
            raw_text="",
            text_char_count=0,
            extraction_method="error",
            fetched_at=fetched_at,
            error=str(exc),
        )


def canonical_domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def extract_from_live_session(
    tab: ChromeTab,
    settings: Settings,
    fetched_at: datetime,
) -> ExtractedContent | None:
    try:
        snapshot = _capture_live_tab_snapshot_with_retry(tab, settings)
    except Exception as exc:  # noqa: BLE001
        logger.info("Live session extraction unavailable for %s: %s", tab.tab_id, exc)
        return None

    raw_text = (str(snapshot.get("text") or "")).strip()
    if not raw_text:
        return None

    final_url = str(snapshot.get("url") or tab.url)
    return ExtractedContent(
        tab_id=tab.tab_id,
        final_url=final_url,
        status_code=200,
        content_type="text/html; source=chrome-session",
        title=str(snapshot.get("title") or tab.title)[:500],
        excerpt=(str(snapshot.get("excerpt") or "")[:280] or None),
        raw_text=raw_text,
        text_char_count=int(snapshot.get("text_char_count") or len(raw_text)),
        extraction_method="chrome_live_dom",
        fetched_at=fetched_at,
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(1),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _capture_live_tab_snapshot_with_retry(
    tab: ChromeTab,
    settings: Settings,
) -> dict[str, str | int | None]:
    return capture_live_tab_snapshot(
        window_index=tab.window_index,
        tab_index=tab.tab_index,
        timeout_seconds=settings.session_extract_timeout_seconds,
        attempts=settings.session_extract_attempts,
    )
