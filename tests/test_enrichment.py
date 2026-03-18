from datetime import UTC, datetime

from chrome_tab_organizer.config import Settings
from chrome_tab_organizer.enrichment import choose_topic, enrich_tabs, rank_pages
from chrome_tab_organizer.models import ChromeTab, ExtractedContent, PageSummary, TabEnrichment


def test_choose_topic_prefers_frequent_candidate() -> None:
    summary = PageSummary(
        summary="A compact summary of a clinical trial page.",
        why_it_matters="Potentially relevant for treatment research.",
        category="oncology research",
        topic_candidates=["oncology research", "clinical trials", "oncology research"],
        key_points=["Point one"],
        follow_up_actions=["Read this page"],
        clinical_relevance=5,
        personal_relevance=2,
        novelty=3,
        urgency=4,
        importance_score=90,
    )
    assert choose_topic(summary) == "Oncology Research"


def test_rank_pages_orders_by_importance() -> None:
    tabs = [
        ChromeTab(
            tab_id="a",
            stable_key="a",
            fingerprint_key="fa",
            window_index=1,
            tab_index=1,
            title="Low",
            url="https://example.com/low",
            domain="example.com",
            discovered_at=datetime.now(UTC),
        ),
        ChromeTab(
            tab_id="b",
            stable_key="b",
            fingerprint_key="fb",
            window_index=1,
            tab_index=2,
            title="High",
            url="https://example.com/high",
            domain="example.com",
            discovered_at=datetime.now(UTC),
        ),
    ]
    enrichments = [
        TabEnrichment(
            tab_id="a",
            topic="General Reference",
            topic_reason="Low priority.",
            summary=PageSummary(
                summary="Summary low priority content here.",
                why_it_matters="Useful but not urgent.",
                category="general reference",
                topic_candidates=["general reference"],
                key_points=["Point"],
                follow_up_actions=["Later"],
                clinical_relevance=1,
                personal_relevance=1,
                novelty=1,
                urgency=1,
                importance_score=20,
            ),
            summarized_at=datetime.now(UTC),
            provider="none",
            model="heuristic",
        ),
        TabEnrichment(
            tab_id="b",
            topic="Oncology Research",
            topic_reason="High priority.",
            summary=PageSummary(
                summary="Summary high priority content here.",
                why_it_matters="Important right now.",
                category="oncology research",
                topic_candidates=["oncology research"],
                key_points=["Point"],
                follow_up_actions=["Read now"],
                clinical_relevance=5,
                personal_relevance=3,
                novelty=4,
                urgency=5,
                importance_score=95,
            ),
            summarized_at=datetime.now(UTC),
            provider="none",
            model="heuristic",
        ),
    ]
    ranked = rank_pages(tabs, enrichments)
    assert ranked[0].tab_id == "b"


def test_enrich_tabs_records_client_provider_and_model(monkeypatch) -> None:
    class FakeClient:
        provider_name = "bedrock"
        model_name = "us.anthropic.claude-sonnet-4-6"

        def summarize_page(self, prompt: str) -> PageSummary:
            return PageSummary(
                summary="A compact summary of a relevant page.",
                why_it_matters="This page is worth reviewing.",
                category="oncology research",
                topic_candidates=["oncology research"],
                key_points=["Key point"],
                follow_up_actions=["Read this page"],
                clinical_relevance=4,
                personal_relevance=2,
                novelty=3,
                urgency=3,
                importance_score=77,
            )

    monkeypatch.setattr("chrome_tab_organizer.enrichment.build_llm_client", lambda settings: FakeClient())

    now = datetime.now(UTC)
    tab = ChromeTab(
        tab_id="tab-1",
        stable_key="tab-1",
        fingerprint_key="fp-1",
        window_index=1,
        tab_index=1,
        title="Crizotinib DrugBank",
        url="https://go.drugbank.com/drugs/DB08865",
        domain="go.drugbank.com",
        discovered_at=now,
    )
    content = ExtractedContent(
        tab_id="tab-1",
        final_url="https://go.drugbank.com/drugs/DB08865",
        status_code=200,
        content_type="text/html; source=chrome-session",
        title="Crizotinib DrugBank",
        excerpt="Crizotinib reference page.",
        raw_text="Crizotinib drug profile and interactions.",
        text_char_count=40,
        extraction_method="chrome_live_dom",
        fetched_at=now,
    )

    enrichments = enrich_tabs([(tab, content)], Settings(provider="bedrock"))
    assert enrichments[0].provider == "bedrock"
    assert enrichments[0].model == "us.anthropic.claude-sonnet-4-6"
