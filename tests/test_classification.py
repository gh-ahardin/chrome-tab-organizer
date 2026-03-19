from datetime import UTC, datetime
from pathlib import Path

from chrome_tab_organizer.cache import SQLiteCache
from chrome_tab_organizer.config import Settings
from chrome_tab_organizer.llm import HeuristicLLMClient, _classification_system_prompt, _extract_classification_list
from chrome_tab_organizer.models import ChromeTab, TabClassification
from chrome_tab_organizer.pipeline import (
    OrganizerPipeline,
    _build_classification_prompt,
    _parse_classification_results,
)


def _make_tab(tab_id: str, title: str, domain: str, tab_index: int = 1) -> ChromeTab:
    now = datetime.now(UTC)
    url = f"https://{domain}/page/{tab_id}"
    return ChromeTab(
        tab_id=tab_id,
        stable_key=tab_id,
        fingerprint_key=tab_id,
        window_index=1,
        tab_index=tab_index,
        title=title,
        url=url,
        domain=domain,
        discovered_at=now,
        first_seen_at=now,
        last_seen_at=now,
    )


# --- batch prompt construction ---

def test_build_classification_prompt_includes_all_tabs() -> None:
    settings = Settings()
    tabs = [
        _make_tab("t1", "Page One", "example.com", 1),
        _make_tab("t2", "Page Two", "github.com", 2),
    ]
    prompt = _build_classification_prompt(tabs, settings)
    assert "t1" in prompt
    assert "t2" in prompt
    assert "Page One" in prompt
    assert "github.com" in prompt


def test_build_classification_prompt_includes_priority_keywords() -> None:
    settings = Settings(priority_keywords=["kubernetes", "helm"])
    tabs = [_make_tab("t1", "K8s Guide", "kubernetes.io")]
    prompt = _build_classification_prompt(tabs, settings)
    assert "kubernetes" in prompt


def test_classification_system_prompt_references_priority_topics() -> None:
    settings = Settings(priority_keywords=["breast cancer", "tnbc"])
    prompt = _classification_system_prompt(settings)
    assert "breast cancer" in prompt
    assert "tnbc" in prompt


# --- result parsing ---

def test_parse_classification_results_maps_to_tabs() -> None:
    tabs = [_make_tab("t1", "Page", "example.com")]
    raw = [{"tab_id": "t1", "topic": "software development", "importance": "high",
            "reason": "Dev tool.", "needs_detailed_summary": True}]
    results = _parse_classification_results(raw, tabs)
    assert len(results) == 1
    assert results[0].tab_id == "t1"
    assert results[0].importance == "high"
    assert results[0].needs_detailed_summary is True


def test_parse_classification_results_defaults_missing_tab() -> None:
    tabs = [_make_tab("t1", "Page", "example.com"), _make_tab("t2", "Other", "other.com")]
    raw = [{"tab_id": "t1", "topic": "research", "importance": "low",
            "reason": "Low priority content.", "needs_detailed_summary": False}]
    results = _parse_classification_results(raw, tabs)
    assert len(results) == 2
    missing = next(r for r in results if r.tab_id == "t2")
    assert missing.importance == "medium"  # default fallback


def test_parse_classification_results_normalizes_invalid_importance() -> None:
    tabs = [_make_tab("t1", "Page", "example.com")]
    raw = [{"tab_id": "t1", "topic": "stuff", "importance": "CRITICAL",
            "reason": "Very important content!", "needs_detailed_summary": False}]
    results = _parse_classification_results(raw, tabs)
    assert results[0].importance == "medium"  # normalized to valid value


def test_extract_classification_list_from_tabs_key() -> None:
    parsed = {"tabs": [{"tab_id": "t1", "topic": "dev", "importance": "low",
                         "reason": "Dev link.", "needs_detailed_summary": False}]}
    result = _extract_classification_list(parsed)
    assert len(result) == 1
    assert result[0]["tab_id"] == "t1"


def test_extract_classification_list_from_bare_list() -> None:
    parsed_list = [{"tab_id": "t1", "topic": "dev", "importance": "low",
                    "reason": "Dev link.", "needs_detailed_summary": False}]
    result = _extract_classification_list(parsed_list)
    assert len(result) == 1


# --- HeuristicLLMClient batch classification ---

def test_heuristic_classify_tabs_batch_produces_results() -> None:
    settings = Settings()
    client = HeuristicLLMClient(settings)
    tabs = [
        _make_tab("t1", "GitHub Repo", "github.com", 1),
        _make_tab("t2", "YouTube Video", "youtube.com", 2),
    ]
    prompt = _build_classification_prompt(tabs, settings)
    raw = client.classify_tabs_batch(prompt)
    tab_ids = [r["tab_id"] for r in raw]
    assert "t1" in tab_ids
    assert "t2" in tab_ids


def test_heuristic_classify_marks_oncology_as_high() -> None:
    settings = Settings()
    client = HeuristicLLMClient(settings)
    tabs = [_make_tab("t1", "Phase 2 Triple Negative Breast Cancer Trial", "clinicaltrials.gov")]
    prompt = _build_classification_prompt(tabs, settings)
    raw = client.classify_tabs_batch(prompt)
    assert raw[0]["importance"] == "high"
    assert raw[0]["needs_detailed_summary"] is True


def test_heuristic_classify_marks_general_reference_as_low() -> None:
    settings = Settings()
    client = HeuristicLLMClient(settings)
    tabs = [_make_tab("t1", "Random Webpage", "totally-unknown-xyz.example")]
    prompt = _build_classification_prompt(tabs, settings)
    raw = client.classify_tabs_batch(prompt)
    assert raw[0]["importance"] == "low"


# --- pipeline classify stage with cache ---

def test_pipeline_classify_saves_classifications(tmp_path: Path) -> None:
    settings = Settings(db_path=tmp_path / "db.sqlite3", output_dir=tmp_path / "out")
    pipeline = OrganizerPipeline(settings)
    now = datetime.now(UTC)
    tabs = [
        ChromeTab(
            tab_id="t1", stable_key="t1", fingerprint_key="f1",
            window_index=1, tab_index=1, title="Clinical Trial",
            url="https://clinicaltrials.gov/study/123", domain="clinicaltrials.gov",
            discovered_at=now,
        ),
        ChromeTab(
            tab_id="t2", stable_key="t2", fingerprint_key="f2",
            window_index=1, tab_index=2, title="Random Page",
            url="https://unknown-site.example/page", domain="unknown-site.example",
            discovered_at=now,
        ),
    ]
    pipeline.cache.upsert_tabs(tabs)

    count = pipeline.classify()
    assert count == 2

    records = pipeline.records()
    assert all(r.classification is not None for r in records)


def test_pipeline_extract_only_fetches_high_priority_after_classify(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(db_path=tmp_path / "db.sqlite3", output_dir=tmp_path / "out")
    pipeline = OrganizerPipeline(settings)
    now = datetime.now(UTC)
    tabs = [
        ChromeTab(
            tab_id="high", stable_key="high", fingerprint_key="fh",
            window_index=1, tab_index=1, title="Breast Cancer Clinical Trial",
            url="https://clinicaltrials.gov/study/123", domain="clinicaltrials.gov",
            discovered_at=now,
        ),
        ChromeTab(
            tab_id="low", stable_key="low", fingerprint_key="fl",
            window_index=1, tab_index=2, title="Pasta Recipe",
            url="https://recipes.example.com/pasta", domain="recipes.example.com",
            discovered_at=now,
        ),
    ]
    pipeline.cache.upsert_tabs(tabs)

    # Manually save classifications: high priority for t1, low for t2
    pipeline.cache.save_classifications([
        TabClassification(tab_id="high", topic="oncology research", importance="high",
                          reason="Priority topic matched.", needs_detailed_summary=True),
        TabClassification(tab_id="low", topic="food and cooking", importance="low",
                          reason="Low priority content.", needs_detailed_summary=False),
    ])

    extracted_tab_ids: list[str] = []

    def fake_extract_tabs(tabs, settings):
        extracted_tab_ids.extend(tab.tab_id for tab in tabs)
        from chrome_tab_organizer.models import ExtractedContent
        return [
            ExtractedContent(
                tab_id=tab.tab_id, final_url=tab.url, status_code=200,
                content_type="text/html", title=tab.title,
                raw_text="Some content.", text_char_count=13,
                extraction_method="test", fetched_at=now,
            )
            for tab in tabs
        ]

    monkeypatch.setattr("chrome_tab_organizer.pipeline.extract_tabs", fake_extract_tabs)
    pipeline.extract()

    # Only the high-priority tab should be extracted
    assert extracted_tab_ids == ["high"]
