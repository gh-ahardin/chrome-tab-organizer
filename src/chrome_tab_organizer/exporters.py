from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
import json
from pathlib import Path
from xml.sax.saxutils import escape

from chrome_tab_organizer.models import PipelineTabRecord, RankedPage, ReportBundle, TopicGroup


def export_markdown_report(
    output_dir: Path,
    records: list[PipelineTabRecord],
    topics: list[TopicGroup],
    top_pages: list[RankedPage],
) -> Path:
    report_path = output_dir / "report.md"
    generated_at = datetime.now(UTC).isoformat()
    lines = [
        "# Chrome Tab Organizer Report",
        "",
        f"Generated at: `{generated_at}`",
        f"Total tabs: `{len(records)}`",
        f"Topics: `{len(topics)}`",
        "",
        "## Top 10 Pages To Read Next",
        "",
    ]
    for page in top_pages:
        lines.extend(
            [
                f"{page.rank}. [{page.title}]({page.url})",
                f"   - Topic: {page.topic}",
                f"   - Importance: {page.importance_score}",
                f"   - Why now: {page.why_read_now}",
            ]
        )
    lines.extend(["", "## Topics", ""])
    records_by_tab_id = {record.tab.tab_id: record for record in records}
    for topic in topics:
        lines.append(f"### {topic.topic}")
        lines.append(topic.description)
        lines.append("")
        for tab_id in topic.tab_ids:
            record = records_by_tab_id[tab_id]
            summary = record.enrichment.summary.summary if record.enrichment else "No summary available."
            lines.append(f"- [{record.tab.title}]({record.tab.url})")
            lines.append(f"  - {summary}")
        lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def export_bookmark_html(output_dir: Path, records: list[PipelineTabRecord]) -> Path:
    bookmark_path = output_dir / "bookmarks_by_topic.html"
    grouped: dict[str, list[PipelineTabRecord]] = defaultdict(list)
    for record in records:
        topic = record.enrichment.topic if record.enrichment else "Uncategorized"
        grouped[topic].append(record)

    lines = [
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        "<TITLE>Bookmarks</TITLE>",
        "<H1>Bookmarks</H1>",
        "<DL><p>",
    ]
    for topic, topic_records in sorted(grouped.items()):
        lines.append(f"<DT><H3>{escape(topic)}</H3>")
        lines.append("<DL><p>")
        for record in topic_records:
            lines.append(
                f'<DT><A HREF="{escape(str(record.tab.url))}">{escape(record.tab.title)}</A>'
            )
        lines.append("</DL><p>")
    lines.append("</DL><p>")
    bookmark_path.write_text("\n".join(lines), encoding="utf-8")
    return bookmark_path


def export_json_snapshot(output_dir: Path, records: list[PipelineTabRecord]) -> Path:
    bundle_path = output_dir / "tabs.json"
    payload = [record.model_dump(mode="json") for record in records]
    bundle_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return bundle_path


def build_report_bundle(
    records: list[PipelineTabRecord],
    topics: list[TopicGroup],
    top_pages: list[RankedPage],
) -> ReportBundle:
    return ReportBundle(
        generated_at=datetime.now(UTC),
        total_tabs=len(records),
        topics=topics,
        top_pages=top_pages,
    )
