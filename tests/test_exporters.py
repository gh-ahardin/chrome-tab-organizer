from datetime import UTC, datetime
from pathlib import Path

from chrome_tab_organizer.exporters import export_bookmark_html, export_markdown_report, export_run_summary
from chrome_tab_organizer.models import (
    ChromeTab,
    ExtractedContent,
    PageSummary,
    PipelineTabRecord,
    RankedPage,
    RunSummary,
    TabEnrichment,
    TopicGroup,
    TabStatus,
)


def sample_record() -> PipelineTabRecord:
    tab = ChromeTab(
        tab_id="stable-1",
        stable_key="stable-1",
        fingerprint_key="fingerprint-1",
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
    run_summary = RunSummary(
        generated_at=datetime.now(UTC),
        total_tabs=1,
        unique_tabs=1,
        duplicate_tabs=0,
        extracted_tabs=1,
        summarized_tabs=1,
        failed_tabs=0,
        live_session_attempted_tabs=1,
        live_session_succeeded_tabs=1,
        live_session_skipped_tabs=0,
        live_session_too_short_tabs=0,
        live_session_failed_tabs=0,
        live_dom_extractions=1,
        http_fallback_extractions=0,
        user_priority_tabs=0,
        topic_count=1,
    )
    report = export_markdown_report(tmp_path, [record], topics, top_pages, run_summary)
    bookmarks = export_bookmark_html(tmp_path, [record])
    summary = export_run_summary(tmp_path, run_summary)
    assert report.exists()
    assert bookmarks.exists()
    assert summary.exists()
    assert "Top 10 Pages To Read Next" in report.read_text(encoding="utf-8")
    assert "Medical Priority Note" in report.read_text(encoding="utf-8")
    assert "Bookmarks" in bookmarks.read_text(encoding="utf-8")


def test_export_bookmark_html_escapes_special_characters(tmp_path: Path) -> None:
    tab = ChromeTab(
        tab_id="xss-1",
        stable_key="xss-1",
        fingerprint_key="fp-xss",
        window_index=1,
        tab_index=1,
        title="<script>alert('xss')</script>",
        url="https://example.com/page?a=1&b=2",
        domain="example.com",
        discovered_at=datetime.now(UTC),
    )
    enrichment = TabEnrichment(
        tab_id="xss-1",
        topic="Dangerous <Topic> & More",
        topic_reason="Testing escaping.",
        summary=PageSummary(
            summary="A compact summary for testing HTML escaping.",
            why_it_matters="Ensures XSS characters are escaped.",
            category="test",
            topic_candidates=["test"],
            key_points=["Escaping works"],
            follow_up_actions=["Verify"],
            clinical_relevance=1,
            personal_relevance=1,
            novelty=1,
            urgency=1,
            importance_score=10,
        ),
        summarized_at=datetime.now(UTC),
        provider="none",
        model="heuristic",
    )
    record = PipelineTabRecord(tab=tab, enrichment=enrichment, status=TabStatus.grouped)
    path = export_bookmark_html(tmp_path, [record])
    content = path.read_text(encoding="utf-8")

    assert "<script>" not in content
    assert "&lt;script&gt;" in content
    assert "&amp;" in content


def test_export_bookmark_html_groups_by_topic(tmp_path: Path) -> None:
    def make_record(tab_id: str, title: str, topic: str) -> PipelineTabRecord:
        tab = ChromeTab(
            tab_id=tab_id,
            stable_key=tab_id,
            fingerprint_key=f"fp-{tab_id}",
            window_index=1,
            tab_index=int(tab_id[-1]),
            title=title,
            url=f"https://example.com/{tab_id}",
            domain="example.com",
            discovered_at=datetime.now(UTC),
        )
        enrichment = TabEnrichment(
            tab_id=tab_id,
            topic=topic,
            topic_reason="Grouped by topic.",
            summary=PageSummary(
                summary="A compact summary of content for topic grouping.",
                why_it_matters="Test grouping.",
                category=topic.lower(),
                topic_candidates=[topic.lower()],
                key_points=["Point"],
                follow_up_actions=["Act"],
                clinical_relevance=1,
                personal_relevance=1,
                novelty=1,
                urgency=1,
                importance_score=30,
            ),
            summarized_at=datetime.now(UTC),
            provider="none",
            model="heuristic",
        )
        return PipelineTabRecord(tab=tab, enrichment=enrichment, status=TabStatus.grouped)

    records = [
        make_record("tab-1", "Page Alpha", "Topic Alpha"),
        make_record("tab-2", "Page Beta", "Topic Beta"),
        make_record("tab-3", "Page Alpha 2", "Topic Alpha"),
    ]
    path = export_bookmark_html(tmp_path, records)
    content = path.read_text(encoding="utf-8")

    assert content.index("Topic Alpha") < content.index("Topic Beta")
    assert content.count("Topic Alpha") >= 1
    assert "Page Alpha" in content
    assert "Page Beta" in content
