"""CutPilot CLI — Typer entrypoint.

Usage:
    cutpilot run <source.mp4>
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog
import typer

from cutpilot.pipeline import run_pipeline

app = typer.Typer(
    name="cutpilot",
    help="Agentic long-video to short-clip generator.",
    no_args_is_help=True,
)
log = structlog.get_logger()


@app.command()
def run(
    source: Path = typer.Argument(..., exists=True, readable=True, help="Input video file."),
    run_id: str = typer.Option("default", help="Run identifier — becomes the output subdirectory."),
) -> None:
    """Run the full CutPilot pipeline: ingest → transcribe → agents → save."""
    log.info("cli.run.start", source=str(source), run_id=run_id)
    manifests = asyncio.run(run_pipeline(source=source, run_id=run_id))
    log.info("cli.run.done", clips=len(manifests))
    for m in manifests:
        typer.echo(f"clip_{m.clip_index}: {m.output_path}")


if __name__ == "__main__":
    app()
