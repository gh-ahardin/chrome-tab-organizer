from __future__ import annotations

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


def enrich_tabs(
    tab_content_pairs: list[tuple[ChromeTab, ExtractedContent]],
    settings: Settings,
) -> list[TabEnrichment]:
    client = build_llm_client(settings)
    enrichments: list[TabEnrichment] = []
    for tab, content in tab_content_pairs:
        prompt = build_summary_prompt(tab, content, settings)
        summary = client.summarize_page(prompt)
        topic = choose_topic(summary)
        enrichments.append(
            TabEnrichment(
                tab_id=tab.tab_id,
                topic=topic,
                topic_reason=summary.why_it_matters,
                summary=summary,
                summarized_at=datetime.now(UTC),
                provider=settings.provider,
                model=settings.model or "heuristic",
            )
        )
    return enrichments


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
        key=lambda item: (
            item.summary.importance_score,
            item.summary.clinical_relevance,
            item.summary.urgency,
            item.summary.novelty,
        ),
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
