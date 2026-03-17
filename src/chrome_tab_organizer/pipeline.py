from __future__ import annotations

import logging
from pathlib import Path

from chrome_tab_organizer.cache import SQLiteCache
from chrome_tab_organizer.chrome import discover_chrome_tabs
from chrome_tab_organizer.config import Settings
from chrome_tab_organizer.enrichment import build_topic_groups, enrich_tabs, rank_pages
from chrome_tab_organizer.exporters import (
    export_bookmark_html,
    export_json_snapshot,
    export_markdown_report,
)
from chrome_tab_organizer.extraction import extract_tabs
from chrome_tab_organizer.models import ChromeTab, PipelineTabRecord

logger = logging.getLogger(__name__)


class OrganizerPipeline:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.ensure_dirs()
        self.cache = SQLiteCache(settings.db_path)

    def discover(self) -> list[ChromeTab]:
        tabs = discover_chrome_tabs()
        if self.settings.max_tabs is not None:
            tabs = tabs[: self.settings.max_tabs]
        self.cache.upsert_tabs(tabs)
        logger.info("Discovered %s tabs", len(tabs))
        return tabs

    def extract(self) -> int:
        tabs = self.cache.get_tabs_missing_content()
        if not tabs:
            logger.info("No tabs need extraction.")
            return 0
        contents = extract_tabs(tabs, self.settings)
        for content in contents:
            self.cache.save_content(content)
        logger.info("Extracted %s tabs", len(contents))
        return len(contents)

    def summarize(self) -> int:
        pending = self.cache.get_tabs_missing_enrichment()
        if not pending:
            logger.info("No tabs need summarization.")
            return 0
        enrichments = enrich_tabs(pending, self.settings)
        for enrichment in enrichments:
            self.cache.save_enrichment(enrichment)
        logger.info("Summarized %s tabs", len(enrichments))
        return len(enrichments)

    def export(self) -> dict[str, Path]:
        records = self.cache.get_tab_records()
        complete_records = [record for record in records if record.enrichment]
        topics = build_topic_groups(record.enrichment for record in complete_records if record.enrichment)
        top_pages = rank_pages(
            tabs=[record.tab for record in complete_records],
            enrichments=[record.enrichment for record in complete_records if record.enrichment],
            limit=10,
        )
        outputs = {
            "report": export_markdown_report(self.settings.output_dir, complete_records, topics, top_pages),
            "bookmarks": export_bookmark_html(self.settings.output_dir, complete_records),
            "json": export_json_snapshot(self.settings.output_dir, records),
        }
        logger.info("Exported report artifacts to %s", self.settings.output_dir)
        return outputs

    def run(self) -> dict[str, Path]:
        self.discover()
        self.extract()
        self.summarize()
        return self.export()

    def records(self) -> list[PipelineTabRecord]:
        return self.cache.get_tab_records()
