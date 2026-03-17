from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Iterator

from chrome_tab_organizer.models import (
    ChromeTab,
    ExtractedContent,
    PipelineTabRecord,
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
                    window_index INTEGER NOT NULL,
                    tab_index INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
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
                """
            )

    def upsert_tabs(self, tabs: list[ChromeTab]) -> None:
        if not tabs:
            return
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO tabs (
                    tab_id, window_index, tab_index, title, url, domain, discovered_at, status, updated_at
                )
                VALUES (:tab_id, :window_index, :tab_index, :title, :url, :domain, :discovered_at, :status, :updated_at)
                ON CONFLICT(tab_id) DO UPDATE SET
                    title=excluded.title,
                    url=excluded.url,
                    domain=excluded.domain,
                    discovered_at=excluded.discovered_at,
                    updated_at=excluded.updated_at
                """,
                [
                    {
                        "tab_id": tab.tab_id,
                        "window_index": tab.window_index,
                        "tab_index": tab.tab_index,
                        "title": tab.title,
                        "url": str(tab.url),
                        "domain": tab.domain,
                        "discovered_at": tab.discovered_at.isoformat(),
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
                    t.window_index,
                    t.tab_index,
                    t.title,
                    t.url,
                    t.domain,
                    t.discovered_at,
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
                window_index=row["window_index"],
                tab_index=row["tab_index"],
                title=row["title"],
                url=row["url"],
                domain=row["domain"],
                discovered_at=datetime.fromisoformat(row["discovered_at"]),
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

    def get_tabs_missing_content(self) -> list[ChromeTab]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT tab_id, window_index, tab_index, title, url, domain, discovered_at
                FROM tabs
                WHERE tab_id NOT IN (SELECT tab_id FROM extracted_content)
                ORDER BY window_index, tab_index
                """
            ).fetchall()
        return [
            ChromeTab(
                tab_id=row["tab_id"],
                window_index=row["window_index"],
                tab_index=row["tab_index"],
                title=row["title"],
                url=row["url"],
                domain=row["domain"],
                discovered_at=datetime.fromisoformat(row["discovered_at"]),
            )
            for row in rows
        ]

    def get_tabs_missing_enrichment(self) -> list[tuple[ChromeTab, ExtractedContent]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    t.tab_id,
                    t.window_index,
                    t.tab_index,
                    t.title,
                    t.url,
                    t.domain,
                    t.discovered_at,
                    ec.payload_json
                FROM tabs t
                JOIN extracted_content ec ON ec.tab_id = t.tab_id
                LEFT JOIN enrichments e ON e.tab_id = t.tab_id
                WHERE e.tab_id IS NULL
                ORDER BY t.window_index, t.tab_index
                """
            ).fetchall()
        return [
            (
                ChromeTab(
                    tab_id=row["tab_id"],
                    window_index=row["window_index"],
                    tab_index=row["tab_index"],
                    title=row["title"],
                    url=row["url"],
                    domain=row["domain"],
                    discovered_at=datetime.fromisoformat(row["discovered_at"]),
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
