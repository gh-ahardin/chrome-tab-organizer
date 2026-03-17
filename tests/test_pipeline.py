from datetime import UTC, datetime

from chrome_tab_organizer.models import (
    ChromeTab,
    ExtractedContent,
    PageSummary,
    PipelineTabRecord,
    TabEnrichment,
    TabStatus,
)
from chrome_tab_organizer.pipeline import OrganizerPipeline


def test_run_summary_counts_duplicates_and_failures(tmp_path) -> None:
    settings_path = tmp_path / "db.sqlite3"
    from chrome_tab_organizer.config import Settings

    pipeline = OrganizerPipeline(Settings(db_path=settings_path, output_dir=tmp_path / "out"))
    discovered_at = datetime.now(UTC)
    canonical = ChromeTab(
        tab_id="t1",
        stable_key="t1",
        fingerprint_key="f1",
        window_index=1,
        tab_index=1,
        title="Clinical trial page",
        url="https://clinicaltrials.gov/study/123",
        domain="clinicaltrials.gov",
        discovered_at=discovered_at,
    )
    duplicate = ChromeTab(
        tab_id="t2",
        stable_key="t2",
        fingerprint_key="f1",
        window_index=1,
        tab_index=2,
        title="Clinical trial page",
        url="https://clinicaltrials.gov/study/123",
        domain="clinicaltrials.gov",
        discovered_at=discovered_at,
        duplicate_of_tab_id="t1",
    )
    failed = ChromeTab(
        tab_id="t3",
        stable_key="t3",
        fingerprint_key="f3",
        window_index=1,
        tab_index=3,
        title="Broken page",
        url="https://example.com/broken",
        domain="example.com",
        discovered_at=discovered_at,
    )
    pipeline.cache.upsert_tabs([canonical, duplicate, failed])
    pipeline.cache.save_content(
        ExtractedContent(
            tab_id="t1",
            final_url=canonical.url,
            status_code=200,
            content_type="text/html",
            title=canonical.title,
            raw_text="Clinical trial content",
            text_char_count=22,
            extraction_method="chrome_live_dom",
            live_session_attempted=True,
            live_session_succeeded=True,
            live_session_text_char_count=22,
            fetched_at=discovered_at,
        )
    )
    pipeline.cache.save_enrichment(
        TabEnrichment(
            tab_id="t1",
            topic="Oncology Research",
            topic_reason="Trial page.",
            summary=PageSummary(
                summary="A structured trial summary for triage.",
                why_it_matters="Relevant to cancer treatment research.",
                category="oncology research",
                topic_candidates=["oncology research"],
                key_points=["Trial details"],
                follow_up_actions=["Review eligibility"],
                clinical_relevance=5,
                personal_relevance=2,
                novelty=3,
                urgency=4,
                importance_score=80,
            ),
            summarized_at=discovered_at,
            provider="none",
            model="heuristic",
        )
    )
    pipeline.cache.save_content(
        ExtractedContent(
            tab_id="t3",
            title=failed.title,
            raw_text="",
            text_char_count=0,
            extraction_method="error",
            live_session_attempted=True,
            live_session_succeeded=False,
            live_session_error="boom",
            fetched_at=discovered_at,
            error="boom",
        )
    )
    summary = pipeline.build_run_summary()
    assert summary.total_tabs == 3
    assert summary.unique_tabs == 2
    assert summary.duplicate_tabs == 1
    assert summary.failed_tabs == 1
    assert summary.live_session_attempted_tabs == 2
    assert summary.live_session_succeeded_tabs == 1
    assert summary.live_session_failed_tabs == 1
    assert summary.live_dom_extractions == 1
    assert summary.medical_priority_tabs == 1


def test_content_duplicate_key_merges_identical_extracted_pages(tmp_path) -> None:
    from chrome_tab_organizer.config import Settings

    pipeline = OrganizerPipeline(Settings(db_path=tmp_path / "db.sqlite3", output_dir=tmp_path / "out"))
    discovered_at = datetime.now(UTC)
    records = [
        PipelineTabRecord(
            tab=ChromeTab(
                tab_id="t1",
                stable_key="t1",
                fingerprint_key="f1",
                window_index=1,
                tab_index=1,
                title="Same",
                url="https://example.com/a",
                domain="example.com",
                discovered_at=discovered_at,
            ),
            content=ExtractedContent(
                tab_id="t1",
                final_url="https://example.com/final",
                title="Same",
                raw_text="identical content",
                text_char_count=17,
                extraction_method="chrome_live_dom",
                fetched_at=discovered_at,
            ),
            status=TabStatus.extracted,
        ),
        PipelineTabRecord(
            tab=ChromeTab(
                tab_id="t2",
                stable_key="t2",
                fingerprint_key="f2",
                window_index=1,
                tab_index=2,
                title="Same",
                url="https://example.com/b",
                domain="example.com",
                discovered_at=discovered_at,
            ),
            content=ExtractedContent(
                tab_id="t2",
                final_url="https://example.com/final",
                title="Same",
                raw_text="identical content",
                text_char_count=17,
                extraction_method="chrome_live_dom",
                fetched_at=discovered_at,
            ),
            status=TabStatus.extracted,
        ),
    ]
    pipeline.cache.upsert_tabs([record.tab for record in records])
    for record in records:
        pipeline.cache.save_content(record.content)
    pipeline._reconcile_content_duplicates(window_index=1)
    updated = pipeline.records()
    duplicates = {record.tab.tab_id: record.tab.duplicate_of_tab_id for record in updated}
    assert duplicates["t1"] is None
    assert duplicates["t2"] == "t1"
