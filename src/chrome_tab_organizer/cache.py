from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Iterator
import uuid

from chrome_tab_organizer.models import (
    ChromeTab,
    ExtractedContent,
    PipelineTabRecord,
    PipelineStage,
    StageRun,
    StageStatus,
    TabEnrichment,
    TabStatus,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class SQLiteCache:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS tabs (
                    tab_id TEXT PRIMARY KEY,
                    stable_key TEXT NOT NULL,
                    fingerprint_key TEXT NOT NULL DEFAULT '',
                    window_index INTEGER NOT NULL,
                    tab_index INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    first_seen_at TEXT,
                    last_seen_at TEXT,
                    duplicate_of_tab_id TEXT,
                    status TEXT NOT NULL DEFAULT 'discovered',
                    last_error TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS extracted_content (
                    tab_id TEXT PRIMARY KEY,
                    payload_json BLOB NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(tab_id) REFERENCES tabs(tab_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS enrichments (
                    tab_id TEXT PRIMARY KEY,
                    payload_json BLOB NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(tab_id) REFERENCES tabs(tab_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS run_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    run_id TEXT PRIMARY KEY,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    details_json TEXT NOT NULL,
                    error TEXT
                );
                """
            )
            self._ensure_columns(conn)

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(tabs)").fetchall()
        }
        expected = {
            "stable_key": "TEXT NOT NULL DEFAULT ''",
            "fingerprint_key": "TEXT NOT NULL DEFAULT ''",
            "first_seen_at": "TEXT",
            "last_seen_at": "TEXT",
            "duplicate_of_tab_id": "TEXT",
        }
        for name, column_type in expected.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE tabs ADD COLUMN {name} {column_type}")

    def upsert_tabs(self, tabs: list[ChromeTab]) -> None:
        if not tabs:
            return
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO tabs (
                    tab_id, stable_key, fingerprint_key, window_index, tab_index, title, url, domain,
                    discovered_at, first_seen_at, last_seen_at, duplicate_of_tab_id, status, updated_at
                )
                VALUES (
                    :tab_id, :stable_key, :fingerprint_key, :window_index, :tab_index, :title, :url,
                    :domain, :discovered_at, :first_seen_at, :last_seen_at, :duplicate_of_tab_id,
                    :status, :updated_at
                )
                ON CONFLICT(tab_id) DO UPDATE SET
                    stable_key=excluded.stable_key,
                    fingerprint_key=excluded.fingerprint_key,
                    title=excluded.title,
                    url=excluded.url,
                    domain=excluded.domain,
                    discovered_at=excluded.discovered_at,
                    window_index=excluded.window_index,
                    tab_index=excluded.tab_index,
                    last_seen_at=excluded.last_seen_at,
                    first_seen_at=COALESCE(tabs.first_seen_at, excluded.first_seen_at),
                    duplicate_of_tab_id=excluded.duplicate_of_tab_id,
                    updated_at=excluded.updated_at
                """,
                [
                    {
                        "tab_id": tab.tab_id,
                        "stable_key": tab.stable_key,
                        "fingerprint_key": tab.fingerprint_key,
                        "window_index": tab.window_index,
                        "tab_index": tab.tab_index,
                        "title": tab.title,
                        "url": str(tab.url),
                        "domain": tab.domain,
                        "discovered_at": tab.discovered_at.isoformat(),
                        "first_seen_at": (tab.first_seen_at or tab.discovered_at).isoformat(),
                        "last_seen_at": (tab.last_seen_at or tab.discovered_at).isoformat(),
                        "duplicate_of_tab_id": tab.duplicate_of_tab_id,
                        "status": TabStatus.discovered.value,
                        "updated_at": _utc_now(),
                    }
                    for tab in tabs
                ],
            )

    def mark_status(self, tab_id: str, status: TabStatus, last_error: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE tabs
                SET status = ?, last_error = ?, updated_at = ?
                WHERE tab_id = ?
                """,
                (status.value, last_error, _utc_now(), tab_id),
            )

    def save_content(self, content: ExtractedContent) -> None:
        payload = json.dumps(content.model_dump(mode="json"))
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO extracted_content (tab_id, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(tab_id) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (content.tab_id, payload, _utc_now()),
            )
        self.mark_status(content.tab_id, TabStatus.extracted, last_error=content.error)

    def save_enrichment(self, enrichment: TabEnrichment) -> None:
        payload = json.dumps(enrichment.model_dump(mode="json"))
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO enrichments (tab_id, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(tab_id) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (enrichment.tab_id, payload, _utc_now()),
            )
        self.mark_status(enrichment.tab_id, TabStatus.grouped)

    def get_tab_records(self) -> list[PipelineTabRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    t.tab_id,
                    t.stable_key,
                    t.fingerprint_key,
                    t.window_index,
                    t.tab_index,
                    t.title,
                    t.url,
                    t.domain,
                    t.discovered_at,
                    t.first_seen_at,
                    t.last_seen_at,
                    t.duplicate_of_tab_id,
                    t.status,
                    ec.payload_json AS content_json,
                    e.payload_json AS enrichment_json
                FROM tabs t
                LEFT JOIN extracted_content ec ON ec.tab_id = t.tab_id
                LEFT JOIN enrichments e ON e.tab_id = t.tab_id
                ORDER BY t.window_index, t.tab_index
                """
            ).fetchall()

        records: list[PipelineTabRecord] = []
        for row in rows:
            tab = ChromeTab(
                tab_id=row["tab_id"],
                stable_key=row["stable_key"],
                fingerprint_key=row["fingerprint_key"],
                window_index=row["window_index"],
                tab_index=row["tab_index"],
                title=row["title"],
                url=row["url"],
                domain=row["domain"],
                discovered_at=datetime.fromisoformat(row["discovered_at"]),
                first_seen_at=(
                    datetime.fromisoformat(row["first_seen_at"]) if row["first_seen_at"] else None
                ),
                last_seen_at=(
                    datetime.fromisoformat(row["last_seen_at"]) if row["last_seen_at"] else None
                ),
                duplicate_of_tab_id=row["duplicate_of_tab_id"],
            )
            content = (
                ExtractedContent.model_validate(json.loads(row["content_json"]))
                if row["content_json"]
                else None
            )
            enrichment = (
                TabEnrichment.model_validate(json.loads(row["enrichment_json"]))
                if row["enrichment_json"]
                else None
            )
            records.append(
                PipelineTabRecord(
                    tab=tab,
                    content=content,
                    enrichment=enrichment,
                    status=TabStatus(row["status"]),
                )
            )
        return records

    def get_tabs_missing_content(self, window_index: int | None = None) -> list[ChromeTab]:
        with self.connect() as conn:
            query = """
                SELECT tab_id, stable_key, window_index, tab_index, title, url, domain, discovered_at,
                       fingerprint_key, first_seen_at, last_seen_at, duplicate_of_tab_id
                FROM tabs
                WHERE tab_id NOT IN (SELECT tab_id FROM extracted_content)
                  AND duplicate_of_tab_id IS NULL
            """
            params: tuple[object, ...] = ()
            if window_index is not None:
                query += " AND window_index = ?"
                params = (window_index,)
            query += " ORDER BY window_index, tab_index"
            rows = conn.execute(query, params).fetchall()
        return [
            ChromeTab(
                tab_id=row["tab_id"],
                stable_key=row["stable_key"],
                fingerprint_key=row["fingerprint_key"],
                window_index=row["window_index"],
                tab_index=row["tab_index"],
                title=row["title"],
                url=row["url"],
                domain=row["domain"],
                discovered_at=datetime.fromisoformat(row["discovered_at"]),
                first_seen_at=(
                    datetime.fromisoformat(row["first_seen_at"]) if row["first_seen_at"] else None
                ),
                last_seen_at=(
                    datetime.fromisoformat(row["last_seen_at"]) if row["last_seen_at"] else None
                ),
                duplicate_of_tab_id=row["duplicate_of_tab_id"],
            )
            for row in rows
        ]

    def get_tabs_missing_enrichment(
        self,
        window_index: int | None = None,
    ) -> list[tuple[ChromeTab, ExtractedContent]]:
        with self.connect() as conn:
            query = """
                SELECT
                    t.tab_id,
                    t.stable_key,
                    t.fingerprint_key,
                    t.window_index,
                    t.tab_index,
                    t.title,
                    t.url,
                    t.domain,
                    t.discovered_at,
                    t.first_seen_at,
                    t.last_seen_at,
                    t.duplicate_of_tab_id,
                    ec.payload_json
                FROM tabs t
                JOIN extracted_content ec ON ec.tab_id = t.tab_id
                LEFT JOIN enrichments e ON e.tab_id = t.tab_id
                WHERE e.tab_id IS NULL
                  AND t.duplicate_of_tab_id IS NULL
            """
            params: tuple[object, ...] = ()
            if window_index is not None:
                query += " AND t.window_index = ?"
                params = (window_index,)
            query += " ORDER BY t.window_index, t.tab_index"
            rows = conn.execute(query, params).fetchall()
        return [
            (
                ChromeTab(
                    tab_id=row["tab_id"],
                    stable_key=row["stable_key"],
                    fingerprint_key=row["fingerprint_key"],
                    window_index=row["window_index"],
                    tab_index=row["tab_index"],
                    title=row["title"],
                    url=row["url"],
                    domain=row["domain"],
                    discovered_at=datetime.fromisoformat(row["discovered_at"]),
                    first_seen_at=(
                        datetime.fromisoformat(row["first_seen_at"]) if row["first_seen_at"] else None
                    ),
                    last_seen_at=(
                        datetime.fromisoformat(row["last_seen_at"]) if row["last_seen_at"] else None
                    ),
                    duplicate_of_tab_id=row["duplicate_of_tab_id"],
                ),
                ExtractedContent.model_validate(json.loads(row["payload_json"])),
            )
            for row in rows
        ]

    def set_run_meta(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO run_meta (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (key, value, _utc_now()),
            )

    def get_run_meta(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM run_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def update_duplicate_links(self, duplicate_map: dict[str, str | None]) -> None:
        if not duplicate_map:
            return
        with self.connect() as conn:
            conn.executemany(
                """
                UPDATE tabs
                SET duplicate_of_tab_id = ?, updated_at = ?
                WHERE tab_id = ?
                """,
                [(duplicate_of, _utc_now(), tab_id) for tab_id, duplicate_of in duplicate_map.items()],
            )

    def start_stage_run(
        self,
        stage: PipelineStage,
        details: dict[str, str | int | float | None] | None = None,
    ) -> str:
        run_id = str(uuid.uuid4())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO pipeline_runs (run_id, stage, status, started_at, details_json, error)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    stage.value,
                    StageStatus.running.value,
                    _utc_now(),
                    json.dumps(details or {}),
                    None,
                ),
            )
        return run_id

    def finish_stage_run(
        self,
        run_id: str,
        details: dict[str, str | int | float | None] | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE pipeline_runs
                SET status = ?, completed_at = ?, details_json = ?
                WHERE run_id = ?
                """,
                (
                    StageStatus.completed.value,
                    _utc_now(),
                    json.dumps(details or {}),
                    run_id,
                ),
            )

    def fail_stage_run(
        self,
        run_id: str,
        error: str,
        details: dict[str, str | int | float | None] | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE pipeline_runs
                SET status = ?, completed_at = ?, details_json = ?, error = ?
                WHERE run_id = ?
                """,
                (
                    StageStatus.failed.value,
                    _utc_now(),
                    json.dumps(details or {}),
                    error,
                    run_id,
                ),
            )

    def interrupt_running_runs(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE pipeline_runs
                SET status = ?, completed_at = ?, error = COALESCE(error, ?)
                WHERE status = ?
                """,
                (
                    StageStatus.interrupted.value,
                    _utc_now(),
                    "Previous process stopped before stage completion.",
                    StageStatus.running.value,
                ),
            )

    def get_stage_runs(self) -> list[StageRun]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, stage, status, started_at, completed_at, details_json, error
                FROM pipeline_runs
                ORDER BY started_at
                """
            ).fetchall()
        return [
            StageRun(
                run_id=row["run_id"],
                stage=PipelineStage(row["stage"]),
                status=StageStatus(row["status"]),
                started_at=datetime.fromisoformat(row["started_at"]),
                completed_at=(
                    datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
                ),
                details=json.loads(row["details_json"]),
                error=row["error"],
            )
            for row in rows
        ]
