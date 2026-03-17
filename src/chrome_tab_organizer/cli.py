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
    dry_run: bool = typer.Option(False, "--dry-run", help="Discover only and skip extraction/summarization/export."),
    sample_tabs: int | None = typer.Option(None, "--sample-tabs", min=1, help="Limit processing to the first N tabs."),
) -> None:
    """Run the full pipeline."""
    settings = Settings.load()
    pipeline = OrganizerPipeline(settings)
    if dry_run:
        try:
            tabs = pipeline.discover(window_index=window_index, sample_tabs=sample_tabs)
        except RuntimeError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1)
        _echo_discovered_tabs(tabs)
        typer.echo("dry_run: extraction, summarization, and export were skipped.")
        summary = pipeline.build_run_summary()
        typer.echo(
            f"summary: total={summary.total_tabs} unique={summary.unique_tabs} duplicates={summary.duplicate_tabs} "
            f"extracted={summary.extracted_tabs} summarized={summary.summarized_tabs} failed={summary.failed_tabs}"
        )
        return

    try:
        outputs = pipeline.run(window_index=window_index, dry_run=False, sample_tabs=sample_tabs)
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    summary = pipeline.build_run_summary()
    typer.echo(
        f"summary: total={summary.total_tabs} unique={summary.unique_tabs} duplicates={summary.duplicate_tabs} "
        f"extracted={summary.extracted_tabs} summarized={summary.summarized_tabs} failed={summary.failed_tabs}"
    )
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
