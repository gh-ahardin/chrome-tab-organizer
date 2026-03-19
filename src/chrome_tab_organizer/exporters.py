from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
import json
from pathlib import Path
from xml.sax.saxutils import escape

from chrome_tab_organizer.models import PipelineTabRecord, RankedPage, ReportBundle, RunSummary, TopicGroup

_MEDICAL_PRIORITY_DISCLAIMER = (
    "This report is a reading and triage aid, not medical advice. "
    "Clinical-trial and oncology pages should be verified against the original source "
    "and discussed with a qualified clinician."
)


def export_markdown_report(
    output_dir: Path,
    records: list[PipelineTabRecord],
    topics: list[TopicGroup],
    top_pages: list[RankedPage],
    run_summary: RunSummary,
    priority_label: str = "medical",
) -> Path:
    report_path = output_dir / "report.md"
    generated_at = datetime.now(UTC).isoformat()
    lines = [
        "# Chrome Tab Organizer Report",
        "",
        f"Generated at: `{generated_at}`",
        f"Total unique tabs in report: `{len(records)}`",
        f"Topics: `{len(topics)}`",
        "",
        f"## {priority_label.capitalize()} Priority Note",
        "",
        _MEDICAL_PRIORITY_DISCLAIMER if priority_label == "medical" else (
            f"Pages flagged as {priority_label} priority have been ranked higher in this report. "
            "Verify important content against the original source."
        ),
        "",
        "## Operator Summary",
        "",
        f"- Total discovered tabs: {run_summary.total_tabs}",
        f"- Unique tabs processed: {run_summary.unique_tabs}",
        f"- Duplicate tabs ignored for processing: {run_summary.duplicate_tabs}",
        f"- Extracted tabs: {run_summary.extracted_tabs}",
        f"- Summarized tabs: {run_summary.summarized_tabs}",
        f"- Failed tabs: {run_summary.failed_tabs}",
        f"- Live session attempted: {run_summary.live_session_attempted_tabs}",
        f"- Live session succeeded: {run_summary.live_session_succeeded_tabs}",
        f"- Live session skipped: {run_summary.live_session_skipped_tabs}",
        f"- Live session too short: {run_summary.live_session_too_short_tabs}",
        f"- Live session failed: {run_summary.live_session_failed_tabs}",
        f"- Live DOM extractions: {run_summary.live_dom_extractions}",
        f"- HTTP fallback extractions: {run_summary.http_fallback_extractions}",
        f"- {priority_label.capitalize()}-priority tabs: {run_summary.user_priority_tabs}",
        "",
        "## Top 10 Pages To Read Next",
        "",
    ]
    if run_summary.top_failure_domains:
        lines.extend(["## Failure Hotspots", ""])
        for item in run_summary.top_failure_domains:
            lines.append(f"- {item.domain}: {item.count}")
        lines.append("")
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
            if record.enrichment:
                summary = record.enrichment.summary.summary
            elif record.classification:
                summary = record.classification.reason
            else:
                summary = "No summary available."
            lines.append(f"- [{record.tab.title}]({record.tab.url})")
            lines.append(f"  - {summary}")
        lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def export_bookmark_html(output_dir: Path, records: list[PipelineTabRecord]) -> Path:
    bookmark_path = output_dir / "bookmarks_by_topic.html"
    grouped: dict[str, list[PipelineTabRecord]] = defaultdict(list)
    for record in records:
        if record.enrichment:
            topic = record.enrichment.topic
        elif record.classification:
            topic = record.classification.topic.title()
        else:
            topic = "Uncategorized"
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


def export_run_summary(output_dir: Path, run_summary: RunSummary) -> Path:
    summary_path = output_dir / "run_summary.json"
    summary_path.write_text(json.dumps(run_summary.model_dump(mode="json"), indent=2), encoding="utf-8")
    return summary_path


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
