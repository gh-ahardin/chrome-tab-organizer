from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any

import httpx
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from chrome_tab_organizer.config import Settings
from chrome_tab_organizer.models import PageSummary

logger = logging.getLogger(__name__)

SUMMARY_LIMITS = {
    "summary": 1200,
    "why_it_matters": 600,
    "category": 120,
}
LIST_LIMITS = {
    "topic_candidates": 8,
    "key_points": 8,
    "follow_up_actions": 6,
}
LIST_ITEM_LIMITS = {
    "topic_candidates": 120,
    "key_points": 240,
    "follow_up_actions": 240,
}


class LLMClient(ABC):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def provider_name(self) -> str:
        return self.settings.provider

    @property
    def model_name(self) -> str:
        return self.settings.model

    @abstractmethod
    def summarize_page(self, prompt: str) -> PageSummary:
        raise NotImplementedError


class NoopLLMClient(LLMClient):
    def summarize_page(self, prompt: str) -> PageSummary:
        raise RuntimeError("No LLM provider configured.")


class HeuristicLLMClient(LLMClient):
    @property
    def provider_name(self) -> str:
        return "none"

    @property
    def model_name(self) -> str:
        return "heuristic"

    def summarize_page(self, prompt: str) -> PageSummary:
        lines = [line.strip() for line in prompt.splitlines() if line.strip()]
        title = next((line.removeprefix("TITLE: ").strip() for line in lines if line.startswith("TITLE: ")), "Untitled")
        domain = next((line.removeprefix("DOMAIN: ").strip() for line in lines if line.startswith("DOMAIN: ")), "unknown")
        text = next((line.removeprefix("TEXT: ").strip() for line in lines if line.startswith("TEXT: ")), "")
        snippet = text[:500] if text else "No extractable text was available."
        category = _infer_category(f"{title} {domain} {snippet}")
        score = _heuristic_score(category, domain, snippet)
        return PageSummary(
            summary=f"{title} from {domain}. {snippet[:320]}",
            why_it_matters="This page appears relevant based on title, domain, and extracted text.",
            category=category,
            topic_candidates=[category, domain],
            key_points=[snippet[:160] or "No key points extracted."],
            follow_up_actions=["Review the original page and decide whether to bookmark or archive it."],
            clinical_relevance=5 if "clinical" in category or "oncology" in category else 1,
            personal_relevance=3 if any(word in category for word in ["linkedin", "personal"]) else 2,
            novelty=2,
            urgency=3 if "trial" in snippet.lower() or "deadline" in snippet.lower() else 1,
            importance_score=score,
        )


class OpenAICompatibleClient(LLMClient):
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((httpx.HTTPError, ValidationError, json.JSONDecodeError)),
        reraise=True,
    )
    def summarize_page(self, prompt: str) -> PageSummary:
        if not self.settings.base_url:
            raise ValueError("CTO_BASE_URL is required for openai_compatible provider.")
        url = self.settings.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.settings.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.settings.api_key}"}
        with httpx.Client(timeout=60) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        return _validate_page_summary(parsed)


class AnthropicClient(LLMClient):
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((httpx.HTTPError, ValidationError, json.JSONDecodeError)),
        reraise=True,
    )
    def summarize_page(self, prompt: str) -> PageSummary:
        payload = {
            "model": self.settings.model,
            "max_tokens": 1200,
            "temperature": 0.1,
            "system": _system_prompt(),
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "x-api-key": self.settings.api_key,
            "anthropic-version": self.settings.anthropic_version,
        }
        with httpx.Client(timeout=60) as client:
            response = client.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers)
            response.raise_for_status()
        body = response.json()
        chunks = body.get("content", [])
        text = "".join(chunk.get("text", "") for chunk in chunks if chunk.get("type") == "text")
        parsed = _extract_json_object(text)
        return _validate_page_summary(parsed)


class BedrockClaudeClient(LLMClient):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        try:
            import boto3
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on local install state
            raise RuntimeError(
                "boto3 is required for the bedrock provider. Install project dependencies first."
            ) from exc
        if not settings.aws_region:
            raise ValueError("CTO_AWS_REGION is required for bedrock provider.")
        model_id = settings.bedrock_model_id or settings.model
        if not model_id:
            raise ValueError("CTO_BEDROCK_MODEL_ID or CTO_MODEL is required for bedrock provider.")
        self.model_id = model_id
        if settings.aws_bearer_token_bedrock:
            os.environ["AWS_BEARER_TOKEN_BEDROCK"] = settings.aws_bearer_token_bedrock
        session_kwargs: dict[str, str] = {"region_name": settings.aws_region}
        if settings.aws_access_key_id:
            session_kwargs["aws_access_key_id"] = settings.aws_access_key_id
        if settings.aws_secret_access_key:
            session_kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
        if settings.aws_session_token:
            session_kwargs["aws_session_token"] = settings.aws_session_token
        session = boto3.session.Session(**session_kwargs)
        self.client = session.client("bedrock-runtime")

    @property
    def model_name(self) -> str:
        return self.model_id

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((ValidationError, json.JSONDecodeError)),
        reraise=True,
    )
    def summarize_page(self, prompt: str) -> PageSummary:
        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1200,
            "temperature": 0.1,
            "system": _system_prompt(),
            "messages": [{"role": "user", "content": prompt}],
        }
        response = self.client.invoke_model(
            modelId=self.model_id,
            body=json.dumps(payload).encode("utf-8"),
            contentType="application/json",
            accept="application/json",
        )
        body = json.loads(response["body"].read())
        chunks = body.get("content", [])
        text = "".join(chunk.get("text", "") for chunk in chunks if chunk.get("type") == "text")
        parsed = _extract_json_object(text)
        return _validate_page_summary(parsed)


def build_llm_client(settings: Settings) -> LLMClient:
    if settings.provider == "none":
        return HeuristicLLMClient(settings)
    if settings.provider == "openai_compatible":
        return OpenAICompatibleClient(settings)
    if settings.provider == "anthropic":
        return AnthropicClient(settings)
    if settings.provider == "bedrock":
        return BedrockClaudeClient(settings)
    raise ValueError(f"Unsupported provider: {settings.provider}")


def _extract_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise json.JSONDecodeError("No JSON object found.", text, 0)
    return json.loads(text[start : end + 1])


def _validate_page_summary(parsed: dict[str, Any]) -> PageSummary:
    return PageSummary.model_validate(_normalize_page_summary_payload(parsed))


def _normalize_page_summary_payload(parsed: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(parsed)
    for field_name, max_length in SUMMARY_LIMITS.items():
        value = normalized.get(field_name)
        if value is not None:
            normalized[field_name] = str(value).strip()[:max_length]

    for field_name, max_items in LIST_LIMITS.items():
        raw_items = normalized.get(field_name)
        if raw_items is None:
            continue
        if not isinstance(raw_items, list):
            raw_items = [raw_items]
        item_limit = LIST_ITEM_LIMITS[field_name]
        normalized[field_name] = [
            str(item).strip()[:item_limit]
            for item in raw_items[:max_items]
            if str(item).strip()
        ]
    return normalized


def _system_prompt() -> str:
    schema = json.dumps(PageSummary.model_json_schema(), indent=2)
    return (
        "You are organizing a large Chrome tab backlog. "
        "Return exactly one JSON object matching this schema. "
        "Be concise, factual, and risk-aware for medical content. "
        "Do not include markdown.\n"
        f"JSON schema:\n{schema}"
    )


def _infer_category(text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in ["triple negative", "breast cancer", "clinical trial", "oncology"]):
        return "oncology research"
    if any(term in lowered for term in ["histopathology", "pathology"]):
        return "histopathology"
    if any(term in lowered for term in ["deep learning", "machine learning", "artificial intelligence", "ai "]):
        return "ai and deep learning"
    if "linkedin" in lowered:
        return "linkedin inspiration"
    return "general reference"


def _heuristic_score(category: str, domain: str, text: str) -> int:
    score = 35
    lowered = f"{category} {domain} {text}".lower()
    keywords = {
        "clinical trial": 20,
        "oncology": 18,
        "triple negative": 18,
        "histopathology": 14,
        "target": 10,
        "drug": 10,
        "linkedin": 6,
        "deadline": 12,
        "important": 8,
    }
    for keyword, weight in keywords.items():
        if keyword in lowered:
            score += weight
    return max(0, min(score, 100))
