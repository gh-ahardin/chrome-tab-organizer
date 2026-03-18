from __future__ import annotations

import concurrent.futures
from collections import Counter
from datetime import UTC, datetime
from typing import Iterable

from chrome_tab_organizer.config import Settings
from chrome_tab_organizer.llm import build_llm_client
from chrome_tab_organizer.models import (
    ChromeTab,
    ExtractedContent,
    RankedPage,
    TabEnrichment,
    TopicGroup,
)

MEDICAL_KEYWORDS = {
    "triple negative",
    "breast cancer",
    "tnbc",
    "clinical trial",
    "oncology",
    "metastatic",
    "histopathology",
    "tumor",
    "drug target",
    "biomarker",
}

MEDICAL_DOMAINS = {
    "clinicaltrials.gov",
    "cancer.gov",
    "pubmed.ncbi.nlm.nih.gov",
    "asco.org",
    "nature.com",
    "nejm.org",
    "thelancet.com",
}


def enrich_tabs(
    tab_content_pairs: list[tuple[ChromeTab, ExtractedContent]],
    settings: Settings,
) -> list[TabEnrichment]:
    client = build_llm_client(settings)
    if settings.llm_max_concurrency <= 1 or len(tab_content_pairs) <= 1:
        return [_enrich_single_tab(tab, content, client, settings) for tab, content in tab_content_pairs]

    enrichments_by_index: dict[int, TabEnrichment] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=settings.llm_max_concurrency) as executor:
        futures = {
            executor.submit(_enrich_single_tab, tab, content, client, settings): index
            for index, (tab, content) in enumerate(tab_content_pairs)
        }
        for future in concurrent.futures.as_completed(futures):
            enrichments_by_index[futures[future]] = future.result()
    return [enrichments_by_index[index] for index in range(len(tab_content_pairs))]


def _enrich_single_tab(
    tab: ChromeTab,
    content: ExtractedContent,
    client,
    settings: Settings,
) -> TabEnrichment:
        prompt = build_summary_prompt(tab, content, settings)
        summary = client.summarize_page(prompt)
        topic = choose_topic(summary)
        return TabEnrichment(
            tab_id=tab.tab_id,
            topic=topic[:120],
            topic_reason=summary.why_it_matters[:400],
            summary=summary,
            summarized_at=datetime.now(UTC),
            provider=client.provider_name,
            model=client.model_name,
        )


def build_summary_prompt(tab: ChromeTab, content: ExtractedContent, settings: Settings) -> str:
    text = content.raw_text[: settings.llm_max_input_chars]
    excerpt = content.excerpt or ""
    return "\n".join(
        [
            "Summarize this browser tab for later triage.",
            f"TITLE: {content.title or tab.title}",
            f"URL: {tab.url}",
            f"DOMAIN: {tab.domain}",
            f"EXCERPT: {excerpt}",
            f"TEXT: {text}",
        ]
    )


def choose_topic(summary_enrichment) -> str:
    candidates = [summary_enrichment.category, *summary_enrichment.topic_candidates]
    normalized = [candidate.strip().lower() for candidate in candidates if candidate.strip()]
    if not normalized:
        return "uncategorized"
    best = Counter(normalized).most_common(1)[0][0]
    return " ".join(part.capitalize() for part in best.split())


def build_topic_groups(
    enrichments: Iterable[TabEnrichment],
) -> list[TopicGroup]:
    grouped: dict[str, list[TabEnrichment]] = {}
    for enrichment in enrichments:
        grouped.setdefault(enrichment.topic, []).append(enrichment)
    topics: list[TopicGroup] = []
    for topic, items in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        descriptions = [item.summary.why_it_matters for item in items[:3]]
        description = " ".join(descriptions)[:500]
        topics.append(
            TopicGroup(
                topic=topic,
                description=description or f"Pages grouped under {topic}.",
                tab_ids=[item.tab_id for item in items],
            )
        )
    return topics


def rank_pages(
    tabs: list[ChromeTab],
    enrichments: list[TabEnrichment],
    limit: int = 10,
) -> list[RankedPage]:
    tab_by_id = {tab.tab_id: tab for tab in tabs}
    ordered = sorted(
        enrichments,
        key=lambda item: page_priority_score(tab_by_id[item.tab_id], item),
        reverse=True,
    )[:limit]
    ranked: list[RankedPage] = []
    for index, enrichment in enumerate(ordered, start=1):
        tab = tab_by_id[enrichment.tab_id]
        ranked.append(
            RankedPage(
                rank=index,
                tab_id=tab.tab_id,
                title=tab.title,
                url=tab.url,
                topic=enrichment.topic,
                importance_score=enrichment.summary.importance_score,
                why_read_now=enrichment.summary.why_it_matters,
            )
        )
    return ranked


def page_priority_score(tab: ChromeTab, enrichment: TabEnrichment) -> tuple[int, int, int, int, int]:
    medical_bonus = 0
    lowered = f"{tab.title} {tab.url} {enrichment.summary.summary} {enrichment.summary.category}".lower()
    if any(keyword in lowered for keyword in MEDICAL_KEYWORDS):
        medical_bonus += 20
    if tab.domain.lower() in MEDICAL_DOMAINS:
        medical_bonus += 15
    return (
        enrichment.summary.importance_score + medical_bonus,
        enrichment.summary.clinical_relevance,
        enrichment.summary.urgency,
        enrichment.summary.novelty,
        enrichment.summary.personal_relevance,
    )


def is_medical_priority(tab: ChromeTab, enrichment: TabEnrichment | None) -> bool:
    if enrichment is None:
        return False
    lowered = f"{tab.title} {tab.url} {enrichment.summary.summary} {enrichment.topic}".lower()
    return any(keyword in lowered for keyword in MEDICAL_KEYWORDS) or tab.domain.lower() in MEDICAL_DOMAINS
