from datetime import datetime, UTC

from chrome_tab_organizer.models import ChromeTab, PageSummary


def test_chrome_tab_model() -> None:
    tab = ChromeTab(
        tab_id="w1-t1",
        stable_key="stable-1",
        fingerprint_key="fingerprint-1",
        window_index=1,
        tab_index=1,
        title="Example",
        url="https://example.com",
        domain="example.com",
        discovered_at=datetime.now(UTC),
    )
    assert tab.domain == "example.com"
    assert tab.stable_key == "stable-1"


def test_page_summary_bounds() -> None:
    summary = PageSummary(
        summary="A" * 40,
        why_it_matters="B" * 30,
        category="oncology",
        topic_candidates=["clinical trials"],
        key_points=["important point"],
        follow_up_actions=["read later"],
        clinical_relevance=5,
        personal_relevance=3,
        novelty=4,
        urgency=2,
        importance_score=88,
    )
    assert summary.importance_score == 88
