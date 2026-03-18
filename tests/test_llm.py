from chrome_tab_organizer.llm import _validate_page_summary


def test_validate_page_summary_clamps_overlong_fields() -> None:
    summary = _validate_page_summary(
        {
            "summary": "S" * 1400,
            "why_it_matters": "W" * 800,
            "category": "C" * 180,
            "topic_candidates": ["T" * 160] * 12,
            "key_points": ["K" * 400] * 12,
            "follow_up_actions": ["F" * 400] * 10,
            "clinical_relevance": 4,
            "personal_relevance": 2,
            "novelty": 3,
            "urgency": 4,
            "importance_score": 88,
        }
    )

    assert len(summary.summary) == 1200
    assert len(summary.why_it_matters) == 600
    assert len(summary.category) == 120
    assert len(summary.topic_candidates) == 8
    assert len(summary.key_points) == 8
    assert len(summary.follow_up_actions) == 6
    assert all(len(item) <= 120 for item in summary.topic_candidates)
    assert all(len(item) <= 240 for item in summary.key_points)
    assert all(len(item) <= 240 for item in summary.follow_up_actions)
