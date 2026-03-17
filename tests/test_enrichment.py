from datetime import UTC, datetime

from chrome_tab_organizer.enrichment import choose_topic, rank_pages
from chrome_tab_organizer.models import ChromeTab, PageSummary, TabEnrichment


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
