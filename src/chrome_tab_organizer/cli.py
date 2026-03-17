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
def run_pipeline() -> None:
    """Run the full pipeline."""
    settings = Settings.load()
    pipeline = OrganizerPipeline(settings)
    outputs = pipeline.run()
    for name, path in outputs.items():
        typer.echo(f"{name}: {path}")


@app.command("discover-tabs")
def discover_tabs() -> None:
    """Discover tabs from Chrome."""
    settings = Settings.load()
    pipeline = OrganizerPipeline(settings)
    tabs = pipeline.discover()
    typer.echo(f"Discovered {len(tabs)} tabs.")


@app.command("summarize")
def summarize() -> None:
    """Summarize extracted tabs."""
    settings = Settings.load()
    pipeline = OrganizerPipeline(settings)
    count = pipeline.summarize()
    typer.echo(f"Summarized {count} tabs.")


@app.command("extract")
def extract() -> None:
    """Fetch and extract content for discovered tabs."""
    settings = Settings.load()
    pipeline = OrganizerPipeline(settings)
    count = pipeline.extract()
    typer.echo(f"Extracted {count} tabs.")


@app.command("export")
def export() -> None:
    """Export bookmarks and reports."""
    settings = Settings.load()
    pipeline = OrganizerPipeline(settings)
    outputs = pipeline.export()
    for name, path in outputs.items():
        typer.echo(f"{name}: {path}")
