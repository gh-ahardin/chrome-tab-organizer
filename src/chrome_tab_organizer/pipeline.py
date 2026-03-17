from __future__ import annotations

import logging
from pathlib import Path
import subprocess
from collections import Counter
import hashlib

from chrome_tab_organizer.cache import SQLiteCache
from chrome_tab_organizer.chrome import discover_window_tabs, get_chrome_window_count, preflight_chrome_access
from chrome_tab_organizer.config import Settings
from chrome_tab_organizer.enrichment import build_topic_groups, enrich_tabs, is_medical_priority, rank_pages
from chrome_tab_organizer.exporters import (
    export_bookmark_html,
    export_json_snapshot,
    export_markdown_report,
    export_run_summary,
)
from chrome_tab_organizer.extraction import extract_tabs
from chrome_tab_organizer.models import (
    ChromeTab,
    FailureDomainStat,
    PipelineStage,
    PipelineTabRecord,
    RunSummary,
)

logger = logging.getLogger(__name__)


class OrganizerPipeline:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.ensure_dirs()
        self.cache = SQLiteCache(settings.db_path)
        self.cache.interrupt_running_runs()

    def discover(self, window_index: int | None = None, sample_tabs: int | None = None) -> list[ChromeTab]:
        run_id = self.cache.start_stage_run(
            PipelineStage.discover,
            details={"window_index": window_index},
        )
        tabs: list[ChromeTab] = []
        occurrence_counts: dict[str, int] = {}
        canonical_ids: dict[str, str] = {}
        try:
            ok, error = preflight_chrome_access()
            if not ok:
                raise RuntimeError(error or "Google Chrome preflight failed.")
            max_windows = get_chrome_window_count()
            target_windows = [window_index] if window_index is not None else list(range(1, max_windows + 1))
            for current_window in target_windows:
                window_tabs = self._discover_window_with_retry(
                    current_window,
                    occurrence_counts,
                    canonical_ids,
                )
                limit = self._effective_limit(sample_tabs)
                if limit is not None:
                    remaining = limit - len(tabs)
                    window_tabs = window_tabs[: max(0, remaining)]
                self.cache.upsert_tabs(window_tabs)
                self.cache.set_run_meta("last_discovered_window_index", str(current_window))
                self.cache.set_run_meta("last_discovered_tab_count", str(len(tabs) + len(window_tabs)))
                tabs.extend(window_tabs)
                if limit is not None and len(tabs) >= limit:
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

    def extract(self, window_index: int | None = None, sample_tabs: int | None = None) -> int:
        run_id = self.cache.start_stage_run(
            PipelineStage.extract,
            details={"window_index": window_index},
        )
        try:
            tabs = self.cache.get_tabs_missing_content(window_index=window_index)
            tabs = self._limit_tabs(tabs, sample_tabs)
            if not tabs:
                logger.info("No tabs need extraction.")
                self.cache.finish_stage_run(run_id, details={"extracted_tabs": 0, "window_index": window_index})
                return 0
            contents = extract_tabs(tabs, self.settings)
            for content in contents:
                self.cache.save_content(content)
            self._reconcile_content_duplicates(window_index=window_index)
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

    def summarize(self, window_index: int | None = None, sample_tabs: int | None = None) -> int:
        run_id = self.cache.start_stage_run(
            PipelineStage.summarize,
            details={"window_index": window_index},
        )
        try:
            pending = self.cache.get_tabs_missing_enrichment(window_index=window_index)
            pending = pending[:sample_tabs] if sample_tabs is not None else pending
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
            run_summary = self.build_run_summary(records)
            outputs = {
                "report": export_markdown_report(
                    self.settings.output_dir,
                    complete_records,
                    topics,
                    top_pages,
                    run_summary,
                ),
                "bookmarks": export_bookmark_html(self.settings.output_dir, complete_records),
                "json": export_json_snapshot(self.settings.output_dir, records),
                "summary": export_run_summary(self.settings.output_dir, run_summary),
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

    def run(
        self,
        window_index: int | None = None,
        *,
        dry_run: bool = False,
        sample_tabs: int | None = None,
    ) -> dict[str, Path]:
        self.discover(window_index=window_index, sample_tabs=sample_tabs)
        if dry_run:
            logger.info("Dry run enabled. Skipping extraction, summarization, and export.")
            return {}
        self.extract(window_index=window_index, sample_tabs=sample_tabs)
        self.summarize(window_index=window_index, sample_tabs=sample_tabs)
        return self.export(window_index=window_index)

    def records(self) -> list[PipelineTabRecord]:
        return self.cache.get_tab_records()

    def build_run_summary(self, records: list[PipelineTabRecord] | None = None) -> RunSummary:
        all_records = records if records is not None else self.cache.get_tab_records()
        unique_records = [record for record in all_records if record.tab.duplicate_of_tab_id is None]
        extracted_records = [record for record in unique_records if record.content is not None]
        summarized_records = [record for record in unique_records if record.enrichment is not None]
        failed_records = [
            record
            for record in unique_records
            if (record.content and record.content.error) or record.status.name == "failed"
        ]
        failure_domains = Counter(record.tab.domain for record in failed_records)
        live_attempted = sum(
            1 for record in extracted_records if record.content and record.content.live_session_attempted
        )
        live_succeeded = sum(
            1 for record in extracted_records if record.content and record.content.live_session_succeeded
        )
        live_skipped = sum(
            1 for record in extracted_records if record.content and record.content.live_session_skipped
        )
        live_too_short = sum(
            1
            for record in extracted_records
            if record.content and record.content.live_session_rejected_as_too_short
        )
        live_failed = sum(
            1
            for record in extracted_records
            if record.content
            and record.content.live_session_attempted
            and not record.content.live_session_succeeded
        )
        live_dom = sum(
            1
            for record in extracted_records
            if record.content and record.content.extraction_method == "chrome_live_dom"
        )
        http_fallback = sum(
            1
            for record in extracted_records
            if record.content and record.content.extraction_method != "chrome_live_dom"
        )
        medical_priority = sum(
            1 for record in summarized_records if is_medical_priority(record.tab, record.enrichment)
        )
        topics = build_topic_groups(
            record.enrichment for record in summarized_records if record.enrichment is not None
        )
        return RunSummary(
            generated_at=self._now(),
            total_tabs=len(all_records),
            unique_tabs=len(unique_records),
            duplicate_tabs=len(all_records) - len(unique_records),
            extracted_tabs=len(extracted_records),
            summarized_tabs=len(summarized_records),
            failed_tabs=len(failed_records),
            live_session_attempted_tabs=live_attempted,
            live_session_succeeded_tabs=live_succeeded,
            live_session_skipped_tabs=live_skipped,
            live_session_too_short_tabs=live_too_short,
            live_session_failed_tabs=live_failed,
            live_dom_extractions=live_dom,
            http_fallback_extractions=http_fallback,
            medical_priority_tabs=medical_priority,
            topic_count=len(topics),
            top_failure_domains=[
                FailureDomainStat(domain=domain, count=count)
                for domain, count in failure_domains.most_common(5)
            ],
        )

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

    def _effective_limit(self, sample_tabs: int | None) -> int | None:
        values = [value for value in [self.settings.max_tabs, sample_tabs] if value is not None]
        return min(values) if values else None

    def _limit_tabs(self, tabs: list[ChromeTab], sample_tabs: int | None) -> list[ChromeTab]:
        limit = self._effective_limit(sample_tabs)
        return tabs[:limit] if limit is not None else tabs

    def _reconcile_content_duplicates(self, window_index: int | None = None) -> None:
        records = self.cache.get_tab_records()
        if window_index is not None:
            records = [record for record in records if record.tab.window_index == window_index]
        ordered = sorted(
            records,
            key=lambda record: (
                record.tab.first_seen_at or record.tab.discovered_at,
                record.tab.window_index,
                record.tab.tab_index,
            ),
        )
        seen: dict[str, str] = {}
        updates: dict[str, str | None] = {}
        for record in ordered:
            key = self._content_duplicate_key(record)
            if key is None:
                continue
            canonical = seen.get(key)
            updates[record.tab.tab_id] = canonical
            if canonical is None:
                seen[key] = record.tab.tab_id
        self.cache.update_duplicate_links(updates)

    def _content_duplicate_key(self, record: PipelineTabRecord) -> str | None:
        content = record.content
        if content is None:
            return None
        url = str(content.final_url or record.tab.url).strip().lower()
        title = (content.title or record.tab.title).strip().lower()
        text = " ".join(content.raw_text.split())[:1200]
        if not url and not text:
            return None
        text_hash = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16] if text else ""
        return f"{url}|{title}|{text_hash}"

    @staticmethod
    def _now():
        from datetime import UTC, datetime

        return datetime.now(UTC)
