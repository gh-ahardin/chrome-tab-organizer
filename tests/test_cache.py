from datetime import UTC, datetime
from pathlib import Path

from chrome_tab_organizer.cache import SQLiteCache
from chrome_tab_organizer.models import (
    ChromeTab,
    ExtractedContent,
    PageSummary,
    PipelineStage,
    StageStatus,
    TabEnrichment,
)


def build_tab() -> ChromeTab:
    return ChromeTab(
        tab_id="stable-1",
        stable_key="stable-1",
        fingerprint_key="fingerprint-1",
        window_index=1,
        tab_index=1,
        title="Clinical trial result",
        url="https://example.com/trial",
        domain="example.com",
        discovered_at=datetime.now(UTC),
    )


def test_sqlite_cache_round_trip(tmp_path: Path) -> None:
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    tab = build_tab()
    cache.upsert_tabs([tab])
    content = ExtractedContent(
        tab_id=tab.tab_id,
        final_url=tab.url,
        status_code=200,
        content_type="text/html",
        title=tab.title,
        raw_text="Important clinical trial update.",
        text_char_count=32,
        extraction_method="test",
        fetched_at=datetime.now(UTC),
    )
    cache.save_content(content)
    enrichment = TabEnrichment(
        tab_id=tab.tab_id,
        topic="Oncology Research",
        topic_reason="Relevant to treatment decisions.",
        summary=PageSummary(
            summary="A structured summary of the page content.",
            why_it_matters="Potentially relevant to care planning.",
            category="oncology research",
            topic_candidates=["oncology research"],
            key_points=["New data presented."],
            follow_up_actions=["Read the methods section."],
            clinical_relevance=5,
            personal_relevance=3,
            novelty=4,
            urgency=4,
            importance_score=92,
        ),
        summarized_at=datetime.now(UTC),
        provider="none",
        model="heuristic",
    )
    cache.save_enrichment(enrichment)

    records = cache.get_tab_records()
    assert len(records) == 1
    assert records[0].enrichment is not None
    assert records[0].enrichment.topic == "Oncology Research"


def test_pipeline_run_journaling_marks_interrupted(tmp_path: Path) -> None:
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    run_id = cache.start_stage_run(PipelineStage.discover, details={"window_index": 1})
    cache.interrupt_running_runs()
    runs = cache.get_stage_runs()
    assert len(runs) == 1
    assert runs[0].run_id == run_id
    assert runs[0].status == StageStatus.interrupted


def test_missing_content_ignores_duplicates(tmp_path: Path) -> None:
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    discovered_at = datetime.now(UTC)
    canonical = ChromeTab(
        tab_id="stable-1",
        stable_key="stable-1",
        fingerprint_key="fingerprint-1",
        window_index=1,
        tab_index=1,
        title="Duplicate page",
        url="https://example.com/page",
        domain="example.com",
        discovered_at=discovered_at,
    )
    duplicate = ChromeTab(
        tab_id="stable-2",
        stable_key="stable-2",
        fingerprint_key="fingerprint-1",
        window_index=1,
        tab_index=2,
        title="Duplicate page",
        url="https://example.com/page",
        domain="example.com",
        discovered_at=discovered_at,
        duplicate_of_tab_id="stable-1",
    )
    cache.upsert_tabs([canonical, duplicate])
    pending = cache.get_tabs_missing_content()
    assert [tab.tab_id for tab in pending] == ["stable-1"]
