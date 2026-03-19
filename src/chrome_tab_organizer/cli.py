from __future__ import annotations

import typer

from chrome_tab_organizer.config import Settings
from chrome_tab_organizer.logging_utils import configure_logging
from chrome_tab_organizer.pipeline import OrganizerPipeline

app = typer.Typer(help="Organize Chrome tabs into summaries, topics, bookmarks, and reports.")


def _echo_discovered_tabs(tabs) -> None:
    if not tabs:
        return
    typer.echo("discovered_tabs:")
    for tab in tabs:
        duplicate_suffix = f" duplicate_of={tab.duplicate_of_tab_id}" if tab.duplicate_of_tab_id else ""
        typer.echo(
            f"- window={tab.window_index} tab={tab.tab_index}{duplicate_suffix}\n"
            f"  title: {tab.title}\n"
            f"  url: {tab.url}"
        )


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", help="Enable debug logging.")) -> None:
    configure_logging(verbose=verbose)


@app.command("run")
def run_pipeline(
    window_index: int | None = typer.Option(
        None,
        "--window-index",
        min=1,
        help="Process only a single Chrome window for safer crash-prone runs.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Discover only and skip classification/extraction/summarization/export."),
    sample_tabs: int | None = typer.Option(None, "--sample-tabs", min=1, help="Limit processing to the first N tabs."),
) -> None:
    """Run the full pipeline."""
    settings = Settings.load()
    pipeline = OrganizerPipeline(settings)

    try:
        tabs = pipeline.discover(window_index=window_index, sample_tabs=sample_tabs)
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    unique_count = sum(1 for t in tabs if t.duplicate_of_tab_id is None)
    duplicate_count = len(tabs) - unique_count
    window_count = len({t.window_index for t in tabs})
    typer.echo(
        f"Found {len(tabs)} tabs across {window_count} window(s). "
        f"{duplicate_count} duplicates detected."
    )

    if dry_run:
        _echo_discovered_tabs(tabs)
        typer.echo("dry_run: classification, extraction, summarization, and export were skipped.")
        return

    try:
        classified_count = pipeline.classify(window_index=window_index, sample_tabs=sample_tabs)
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    records = pipeline.records()
    classifications = [r.classification for r in records if r.classification]
    high_count = sum(1 for c in classifications if c.importance == "high")
    medium_count = sum(1 for c in classifications if c.importance == "medium")
    low_count = sum(1 for c in classifications if c.importance == "low")
    detail_count = sum(1 for c in classifications if c.needs_detailed_summary)
    priority_count = sum(
        1 for r in records
        if r.classification and r.classification.importance == "high"
        and any(kw in (r.tab.title + str(r.tab.url)).lower() for kw in settings.priority_keywords)
    )
    topics = {c.topic for c in classifications}
    typer.echo(
        f"Classified {classified_count} tabs into {len(topics)} topics. "
        f"{high_count} high / {medium_count} medium / {low_count} low importance."
    )
    if detail_count:
        typer.echo(f"  {detail_count} tabs flagged for detailed content extraction.")
    if priority_count:
        typer.echo(f"  {priority_count} tabs match your {settings.priority_label} priority topics.")

    try:
        extracted_count = pipeline.extract(window_index=window_index, sample_tabs=sample_tabs)
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    if extracted_count:
        typer.echo(f"Extracted content for {extracted_count} priority tabs.")

    try:
        summarized_count = pipeline.summarize(window_index=window_index, sample_tabs=sample_tabs)
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    if summarized_count:
        typer.echo(f"Generated detailed summaries for {summarized_count} tabs.")

    try:
        outputs = pipeline.export(window_index=window_index)
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    typer.echo("")
    for name, path in outputs.items():
        typer.echo(f"{name}: {path}")


@app.command("discover-tabs")
def discover_tabs(
    window_index: int | None = typer.Option(None, "--window-index", min=1),
    sample_tabs: int | None = typer.Option(None, "--sample-tabs", min=1),
) -> None:
    """Discover tabs from Chrome."""
    settings = Settings.load()
    pipeline = OrganizerPipeline(settings)
    try:
        tabs = pipeline.discover(window_index=window_index, sample_tabs=sample_tabs)
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    unique_count = len([tab for tab in tabs if tab.duplicate_of_tab_id is None])
    typer.echo(f"Discovered {len(tabs)} tabs ({unique_count} unique).")
    _echo_discovered_tabs(tabs)


@app.command("classify")
def classify(
    window_index: int | None = typer.Option(None, "--window-index", min=1),
    sample_tabs: int | None = typer.Option(None, "--sample-tabs", min=1),
) -> None:
    """Batch-classify discovered tabs by title, URL, and domain."""
    settings = Settings.load()
    pipeline = OrganizerPipeline(settings)
    try:
        count = pipeline.classify(window_index=window_index, sample_tabs=sample_tabs)
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Classified {count} tabs.")


@app.command("summarize")
def summarize(
    window_index: int | None = typer.Option(None, "--window-index", min=1),
    sample_tabs: int | None = typer.Option(None, "--sample-tabs", min=1),
) -> None:
    """Summarize extracted tabs."""
    settings = Settings.load()
    pipeline = OrganizerPipeline(settings)
    try:
        count = pipeline.summarize(window_index=window_index, sample_tabs=sample_tabs)
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Summarized {count} tabs.")


@app.command("extract")
def extract(
    window_index: int | None = typer.Option(None, "--window-index", min=1),
    sample_tabs: int | None = typer.Option(None, "--sample-tabs", min=1),
) -> None:
    """Fetch and extract content for discovered tabs."""
    settings = Settings.load()
    pipeline = OrganizerPipeline(settings)
    try:
        count = pipeline.extract(window_index=window_index, sample_tabs=sample_tabs)
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Extracted {count} tabs.")


@app.command("export")
def export(
    window_index: int | None = typer.Option(None, "--window-index", min=1),
) -> None:
    """Export bookmarks and reports."""
    settings = Settings.load()
    pipeline = OrganizerPipeline(settings)
    try:
        outputs = pipeline.export(window_index=window_index)
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    for name, path in outputs.items():
        typer.echo(f"{name}: {path}")
