from __future__ import annotations

import logging
from pathlib import Path
import subprocess

from chrome_tab_organizer.cache import SQLiteCache
from chrome_tab_organizer.chrome import discover_window_tabs, get_chrome_window_count
from chrome_tab_organizer.config import Settings
from chrome_tab_organizer.enrichment import build_topic_groups, enrich_tabs, rank_pages
from chrome_tab_organizer.exporters import (
    export_bookmark_html,
    export_json_snapshot,
    export_markdown_report,
)
from chrome_tab_organizer.extraction import extract_tabs
from chrome_tab_organizer.models import ChromeTab, PipelineStage, PipelineTabRecord

logger = logging.getLogger(__name__)


class OrganizerPipeline:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.ensure_dirs()
        self.cache = SQLiteCache(settings.db_path)
        self.cache.interrupt_running_runs()

    def discover(self, window_index: int | None = None) -> list[ChromeTab]:
        run_id = self.cache.start_stage_run(
            PipelineStage.discover,
            details={"window_index": window_index},
        )
        tabs: list[ChromeTab] = []
        occurrence_counts: dict[str, int] = {}
        canonical_ids: dict[str, str] = {}
        try:
            max_windows = get_chrome_window_count()
            target_windows = [window_index] if window_index is not None else list(range(1, max_windows + 1))
            for current_window in target_windows:
                window_tabs = self._discover_window_with_retry(
                    current_window,
                    occurrence_counts,
                    canonical_ids,
                )
                if self.settings.max_tabs is not None:
                    remaining = self.settings.max_tabs - len(tabs)
                    window_tabs = window_tabs[: max(0, remaining)]
                self.cache.upsert_tabs(window_tabs)
                self.cache.set_run_meta("last_discovered_window_index", str(current_window))
                self.cache.set_run_meta("last_discovered_tab_count", str(len(tabs) + len(window_tabs)))
                tabs.extend(window_tabs)
                if self.settings.max_tabs is not None and len(tabs) >= self.settings.max_tabs:
                    break
            self.cache.finish_stage_run(
                run_id,
                details={"discovered_tabs": len(tabs), "window_index": window_index},
            )
            unique_count = len([tab for tab in tabs if tab.duplicate_of_tab_id is None])
            logger.info("Discovered %s tabs (%s unique)", len(tabs), unique_count)
            return tabs
        except Exception as exc:  # noqa: BLE001
            self.cache.fail_stage_run(
                run_id,
                error=str(exc),
                details={"discovered_tabs": len(tabs), "window_index": window_index},
            )
            raise

    def extract(self, window_index: int | None = None) -> int:
        run_id = self.cache.start_stage_run(
            PipelineStage.extract,
            details={"window_index": window_index},
        )
        try:
            tabs = self.cache.get_tabs_missing_content(window_index=window_index)
            if not tabs:
                logger.info("No tabs need extraction.")
                self.cache.finish_stage_run(run_id, details={"extracted_tabs": 0, "window_index": window_index})
                return 0
            contents = extract_tabs(tabs, self.settings)
            for content in contents:
                self.cache.save_content(content)
            self.cache.finish_stage_run(
                run_id,
                details={"extracted_tabs": len(contents), "window_index": window_index},
            )
            logger.info("Extracted %s tabs", len(contents))
            return len(contents)
        except Exception as exc:  # noqa: BLE001
            self.cache.fail_stage_run(
                run_id,
                error=str(exc),
                details={"window_index": window_index},
            )
            raise

    def summarize(self, window_index: int | None = None) -> int:
        run_id = self.cache.start_stage_run(
            PipelineStage.summarize,
            details={"window_index": window_index},
        )
        try:
            pending = self.cache.get_tabs_missing_enrichment(window_index=window_index)
            if not pending:
                logger.info("No tabs need summarization.")
                self.cache.finish_stage_run(
                    run_id,
                    details={"summarized_tabs": 0, "window_index": window_index},
                )
                return 0
            enrichments = enrich_tabs(pending, self.settings)
            for enrichment in enrichments:
                self.cache.save_enrichment(enrichment)
            self.cache.finish_stage_run(
                run_id,
                details={"summarized_tabs": len(enrichments), "window_index": window_index},
            )
            logger.info("Summarized %s tabs", len(enrichments))
            return len(enrichments)
        except Exception as exc:  # noqa: BLE001
            self.cache.fail_stage_run(
                run_id,
                error=str(exc),
                details={"window_index": window_index},
            )
            raise

    def export(self, window_index: int | None = None) -> dict[str, Path]:
        run_id = self.cache.start_stage_run(
            PipelineStage.export,
            details={"window_index": window_index},
        )
        try:
            records = self.cache.get_tab_records()
            if window_index is not None:
                records = [record for record in records if record.tab.window_index == window_index]
            unique_records = [record for record in records if record.tab.duplicate_of_tab_id is None]
            complete_records = [record for record in unique_records if record.enrichment]
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
            self.cache.finish_stage_run(
                run_id,
                details={"exported_tabs": len(records), "window_index": window_index},
            )
            logger.info("Exported report artifacts to %s", self.settings.output_dir)
            return outputs
        except Exception as exc:  # noqa: BLE001
            self.cache.fail_stage_run(
                run_id,
                error=str(exc),
                details={"window_index": window_index},
            )
            raise

    def run(self, window_index: int | None = None) -> dict[str, Path]:
        self.discover(window_index=window_index)
        self.extract(window_index=window_index)
        self.summarize(window_index=window_index)
        return self.export(window_index=window_index)

    def records(self) -> list[PipelineTabRecord]:
        return self.cache.get_tab_records()

    def _discover_window_with_retry(
        self,
        current_window: int,
        occurrence_counts: dict[str, int],
        canonical_ids: dict[str, str],
    ) -> list[ChromeTab]:
        last_error: Exception | None = None
        for _ in range(max(1, self.settings.discovery_attempts)):
            try:
                return discover_window_tabs(
                    current_window,
                    occurrence_counts=occurrence_counts,
                    canonical_ids=canonical_ids,
                )
            except subprocess.CalledProcessError as exc:
                last_error = exc
                logger.warning("Window %s discovery failed, retrying: %s", current_window, exc)
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Window {current_window} discovery failed without an error.")
