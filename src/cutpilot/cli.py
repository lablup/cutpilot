"""CutPilot CLI — Typer entrypoint.

Usage:
    cutpilot <source>                          # local file or URL
    cutpilot https://youtu.be/cW_POtTfJVM
    cutpilot /path/to/video.mp4 --run-id demo
"""

from __future__ import annotations

import asyncio

import structlog
import typer

from cutpilot.pipeline import run_pipeline

log = structlog.get_logger()


def main(
    source: str = typer.Argument(
        ...,
        help="Local video file path or an http(s) URL (YouTube, Vimeo, etc.).",
    ),
    run_id: str = typer.Option(
        "default",
        "--run-id",
        help="Run identifier — becomes the output subdirectory.",
    ),
    burn_captions: bool = typer.Option(
        False,
        "--burn-captions/--no-burn-captions",
        help="Burn captions onto the clips as overlays. Off by default; "
        "caption text is always saved to the manifest regardless.",
    ),
) -> None:
    """Run the full CutPilot pipeline: ingest → transcribe → agents → save."""
    log.info("cli.run.start", source=source, run_id=run_id, burn_captions=burn_captions)
    manifests = asyncio.run(
        run_pipeline(source=source, run_id=run_id, burn_captions=burn_captions)
    )
    log.info("cli.run.done", clips=len(manifests))
    for manifest in manifests:
        typer.echo(f"clip_{manifest.clip_index}: {manifest.output_path}")


def app() -> None:
    """Entry point referenced by `[project.scripts]` in pyproject.toml.

    `typer.run` constructs a single-command Typer app, so `cutpilot <source>`
    works without a subcommand name. Using `@app.callback()` instead makes Click
    run in group mode, which requires a subcommand and breaks positional
    parsing for single-command CLIs.
    """
    typer.run(main)


def _serve_cmd(
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
    port: int = typer.Option(8080, help="Port to listen on."),
    reload: bool = typer.Option(False, help="Auto-reload on code changes (dev only)."),
) -> None:
    """Start the FastAPI HTTP server that backs the review UI."""
    # Imported lazily so `cutpilot <source>` doesn't pay the FastAPI import cost.
    import uvicorn

    log.info("cli.serve.start", host=host, port=port, reload=reload)
    uvicorn.run("cutpilot.server:app", host=host, port=port, reload=reload)


def serve() -> None:
    """Entry point for `cutpilot-serve`."""
    typer.run(_serve_cmd)


if __name__ == "__main__":
    app()
