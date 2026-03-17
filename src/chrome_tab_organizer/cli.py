from __future__ import annotations

import typer

from chrome_tab_organizer.config import Settings
from chrome_tab_organizer.logging_utils import configure_logging
from chrome_tab_organizer.pipeline import OrganizerPipeline

app = typer.Typer(help="Organize Chrome tabs into summaries, topics, bookmarks, and reports.")


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
) -> None:
    """Run the full pipeline."""
    settings = Settings.load()
    pipeline = OrganizerPipeline(settings)
    outputs = pipeline.run(window_index=window_index)
    for name, path in outputs.items():
        typer.echo(f"{name}: {path}")


@app.command("discover-tabs")
def discover_tabs(
    window_index: int | None = typer.Option(None, "--window-index", min=1),
) -> None:
    """Discover tabs from Chrome."""
    settings = Settings.load()
    pipeline = OrganizerPipeline(settings)
    tabs = pipeline.discover(window_index=window_index)
    typer.echo(f"Discovered {len(tabs)} tabs.")


@app.command("summarize")
def summarize(
    window_index: int | None = typer.Option(None, "--window-index", min=1),
) -> None:
    """Summarize extracted tabs."""
    settings = Settings.load()
    pipeline = OrganizerPipeline(settings)
    count = pipeline.summarize(window_index=window_index)
    typer.echo(f"Summarized {count} tabs.")


@app.command("extract")
def extract(
    window_index: int | None = typer.Option(None, "--window-index", min=1),
) -> None:
    """Fetch and extract content for discovered tabs."""
    settings = Settings.load()
    pipeline = OrganizerPipeline(settings)
    count = pipeline.extract(window_index=window_index)
    typer.echo(f"Extracted {count} tabs.")


@app.command("export")
def export(
    window_index: int | None = typer.Option(None, "--window-index", min=1),
) -> None:
    """Export bookmarks and reports."""
    settings = Settings.load()
    pipeline = OrganizerPipeline(settings)
    outputs = pipeline.export(window_index=window_index)
    for name, path in outputs.items():
        typer.echo(f"{name}: {path}")
