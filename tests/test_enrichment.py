from datetime import UTC, datetime

from chrome_tab_organizer.config import Settings
from chrome_tab_organizer.enrichment import choose_topic, enrich_tabs, is_user_priority, page_priority_score, rank_pages
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
    ranked = rank_pages(tabs, enrichments, settings=Settings())
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


def _make_tab(tab_id: str, title: str, url: str, domain: str) -> ChromeTab:
    return ChromeTab(
        tab_id=tab_id, stable_key=tab_id, fingerprint_key=tab_id,
        window_index=1, tab_index=1, title=title, url=url, domain=domain,
        discovered_at=datetime.now(UTC),
    )


def _make_enrichment(tab_id: str, topic: str, summary_text: str, score: int = 50) -> TabEnrichment:
    return TabEnrichment(
        tab_id=tab_id, topic=topic, topic_reason="Test reason.",
        summary=PageSummary(
            summary=f"Summary of {summary_text[:30]}.",
            why_it_matters="Relevant for testing.",
            category=topic.lower(),
            topic_candidates=[topic.lower()],
            key_points=["Point"],
            follow_up_actions=["Act"],
            clinical_relevance=2, personal_relevance=2, novelty=2, urgency=2,
            importance_score=score,
        ),
        summarized_at=datetime.now(UTC), provider="none", model="heuristic",
    )


# --- page_priority_score with configurable settings ---

def test_page_priority_score_applies_default_oncology_bonus() -> None:
    settings = Settings()
    tab = _make_tab("t1", "Breast Cancer Clinical Trial", "https://clinicaltrials.gov/", "clinicaltrials.gov")
    enrichment = _make_enrichment("t1", "Oncology Research", "breast cancer treatment", score=60)
    base_score, *_ = page_priority_score(tab, enrichment, settings)
    # Should get both keyword bonus (+20) and domain bonus (+15) on top of importance_score 60
    assert base_score >= 60 + 15  # at minimum domain bonus


def test_page_priority_score_applies_custom_keyword_bonus() -> None:
    settings = Settings(
        priority_keywords=["kubernetes", "deployment"],
        priority_domains=[],
        priority_keyword_score_bonus=25,
        priority_domain_score_bonus=10,
    )
    tab = _make_tab("t1", "Kubernetes Deployment Guide", "https://docs.example.com/k8s", "docs.example.com")
    enrichment = _make_enrichment("t1", "DevOps", "kubernetes deployment strategies", score=50)
    base_score, *_ = page_priority_score(tab, enrichment, settings)
    assert base_score == 75  # 50 + 25 keyword bonus


def test_page_priority_score_no_bonus_for_non_matching_tab() -> None:
    settings = Settings(
        priority_keywords=["kubernetes"],
        priority_domains=["k8s.io"],
        priority_keyword_score_bonus=20,
        priority_domain_score_bonus=15,
    )
    tab = _make_tab("t1", "Pasta Recipe", "https://recipes.example.com/pasta", "recipes.example.com")
    enrichment = _make_enrichment("t1", "Cooking", "pasta recipe", score=30)
    base_score, *_ = page_priority_score(tab, enrichment, settings)
    assert base_score == 30  # no bonus applied


# --- is_user_priority ---

def test_is_user_priority_matches_on_keyword_in_title() -> None:
    settings = Settings()
    tab = _make_tab("t1", "Triple Negative Breast Cancer Study", "https://example.com/", "example.com")
    enrichment = _make_enrichment("t1", "Oncology", "study results", score=70)
    assert is_user_priority(tab, enrichment, settings) is True


def test_is_user_priority_matches_on_priority_domain() -> None:
    settings = Settings()
    tab = _make_tab("t1", "Study NCT12345", "https://clinicaltrials.gov/study/NCT12345", "clinicaltrials.gov")
    enrichment = _make_enrichment("t1", "Research", "clinical study", score=50)
    assert is_user_priority(tab, enrichment, settings) is True


def test_is_user_priority_false_for_unrelated_tab() -> None:
    settings = Settings()
    tab = _make_tab("t1", "Best Pasta Recipes", "https://food.example.com/pasta", "food.example.com")
    enrichment = _make_enrichment("t1", "Cooking", "pasta guide", score=20)
    assert is_user_priority(tab, enrichment, settings) is False


def test_is_user_priority_uses_custom_keywords() -> None:
    settings = Settings(priority_keywords=["kubernetes", "helm chart"], priority_domains=[])
    tab = _make_tab("t1", "Helm Chart Best Practices", "https://helm.sh/docs", "helm.sh")
    enrichment = _make_enrichment("t1", "DevOps", "helm chart configuration", score=55)
    assert is_user_priority(tab, enrichment, settings) is True


def test_is_user_priority_returns_false_when_no_enrichment() -> None:
    settings = Settings()
    tab = _make_tab("t1", "Breast Cancer Trial", "https://clinicaltrials.gov/", "clinicaltrials.gov")
    assert is_user_priority(tab, None, settings) is False
