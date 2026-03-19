import json

import pytest

from chrome_tab_organizer.config import Settings
from chrome_tab_organizer.llm import (
    HeuristicLLMClient,
    _extract_json_object,
    _system_prompt,
    _validate_page_summary,
)
from chrome_tab_organizer.models import PageSummary


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


# --- _extract_json_object ---

def test_extract_json_object_from_plain_json() -> None:
    text = '{"summary": "hello", "importance_score": 50}'
    result = _extract_json_object(text)
    assert result["summary"] == "hello"


def test_extract_json_object_from_json_wrapped_in_prose() -> None:
    text = 'Here is the output: {"summary": "hello", "importance_score": 50} done.'
    result = _extract_json_object(text)
    assert result["summary"] == "hello"


def test_extract_json_object_raises_when_no_json_present() -> None:
    with pytest.raises(json.JSONDecodeError):
        _extract_json_object("No JSON here at all.")


# --- _system_prompt ---

def test_system_prompt_includes_schema_fields() -> None:
    prompt = _system_prompt()
    assert "importance_score" in prompt
    assert "summary" in prompt
    assert "why_it_matters" in prompt


def test_system_prompt_instructs_json_only() -> None:
    prompt = _system_prompt()
    assert "JSON" in prompt
    assert "markdown" in prompt.lower()


# --- HeuristicLLMClient ---

def _make_prompt(title: str, domain: str, text: str) -> str:
    return "\n".join([
        "Summarize this browser tab for later triage.",
        f"TITLE: {title}",
        f"URL: https://{domain}/",
        f"DOMAIN: {domain}",
        "EXCERPT: ",
        f"TEXT: {text}",
    ])


def test_heuristic_client_produces_valid_page_summary() -> None:
    client = HeuristicLLMClient(Settings())
    prompt = _make_prompt("Example Page", "example.com", "Some page content here.")
    result = client.summarize_page(prompt)
    assert isinstance(result, PageSummary)
    assert len(result.summary) >= 20
    assert 0 <= result.importance_score <= 100


def test_heuristic_client_scores_oncology_content_higher() -> None:
    client = HeuristicLLMClient(Settings())
    oncology_prompt = _make_prompt(
        "Phase 2 Breast Cancer Trial",
        "clinicaltrials.gov",
        "Triple negative breast cancer patients showed improved outcomes in this clinical trial.",
    )
    general_prompt = _make_prompt(
        "Pasta Recipe",
        "recipes.example.com",
        "Boil water, add pasta, cook for 10 minutes.",
    )
    oncology_result = client.summarize_page(oncology_prompt)
    general_result = client.summarize_page(general_prompt)
    assert oncology_result.importance_score > general_result.importance_score
