from datetime import UTC, datetime
from pathlib import Path

from chrome_tab_organizer.exporters import export_bookmark_html, export_markdown_report
from chrome_tab_organizer.models import (
    ChromeTab,
    ExtractedContent,
    PageSummary,
    PipelineTabRecord,
    RankedPage,
    TabEnrichment,
    TopicGroup,
    TabStatus,
)


def sample_record() -> PipelineTabRecord:
    tab = ChromeTab(
        tab_id="stable-1",
        stable_key="stable-1",
        window_index=1,
        tab_index=1,
        title="Example title",
        url="https://example.com",
        domain="example.com",
        discovered_at=datetime.now(UTC),
    )
    content = ExtractedContent(
        tab_id=tab.tab_id,
        final_url=tab.url,
        status_code=200,
        content_type="text/html",
        title=tab.title,
        raw_text="Example text",
        text_char_count=12,
        extraction_method="test",
        fetched_at=datetime.now(UTC),
    )
    enrichment = TabEnrichment(
        tab_id=tab.tab_id,
        topic="General Reference",
        topic_reason="Useful background page.",
        summary=PageSummary(
            summary="Useful background page.",
            why_it_matters="Helps frame the rest of the research set.",
            category="general reference",
            topic_candidates=["general reference"],
            key_points=["Example point"],
            follow_up_actions=["Review later"],
            clinical_relevance=1,
            personal_relevance=2,
            novelty=2,
            urgency=1,
            importance_score=42,
        ),
        summarized_at=datetime.now(UTC),
        provider="none",
        model="heuristic",
    )
    return PipelineTabRecord(
        tab=tab,
        content=content,
        enrichment=enrichment,
        status=TabStatus.grouped,
    )


def test_exporters_write_files(tmp_path: Path) -> None:
    record = sample_record()
    topics = [TopicGroup(topic="General Reference", description="Useful pages.", tab_ids=[record.tab.tab_id])]
    top_pages = [
        RankedPage(
            rank=1,
            tab_id=record.tab.tab_id,
            title=record.tab.title,
            url=record.tab.url,
            topic="General Reference",
            importance_score=42,
            why_read_now="Useful context.",
        )
    ]
    report = export_markdown_report(tmp_path, [record], topics, top_pages)
    bookmarks = export_bookmark_html(tmp_path, [record])
    assert report.exists()
    assert bookmarks.exists()
    assert "Top 10 Pages To Read Next" in report.read_text(encoding="utf-8")
    assert "Bookmarks" in bookmarks.read_text(encoding="utf-8")
