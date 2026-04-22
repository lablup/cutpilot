"""Boot cutpilot-serve with a completed run preloaded in memory, so the review
UI can be opened at `http://127.0.0.1:PORT/?run_id=<id>` and screenshotted.

Reads manifests from `outputs/<run_id>/clip_*.manifest.json` and seeds
`server._RUNS` before uvicorn starts. The server stays in the foreground —
Ctrl-C (or the external screenshot driver) stops it.

Run: `python scripts/screenshot_ui_run.py <run_id> [--port 8080]`
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import typer
import uvicorn

from cutpilot import paths, persistence
from cutpilot.models import ClipManifest
from cutpilot.server import RunState, RunStatus, _RUNS, app


def main(
    run_id: str = typer.Argument(..., help="Existing run id under outputs/."),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8080),
) -> None:
    run_dir = paths.outputs_root() / run_id
    if not run_dir.exists():
        raise typer.BadParameter(f"outputs/{run_id} does not exist")

    manifests: list[ClipManifest] = []
    for clip_index in (1, 2, 3):
        mpath = paths.clip_manifest_path(run_id, clip_index)
        if not mpath.exists():
            raise typer.BadParameter(f"missing manifest: {mpath}")
        manifests.append(persistence.load(ClipManifest, mpath))

    _RUNS[run_id] = RunState(
        run_id=run_id,
        status=RunStatus.DONE,
        source=str(manifests[0].source_path) if manifests else "",
        burn_captions=False,
        created_at=datetime.now(UTC),
        manifests=manifests,
    )
    typer.echo(f"preloaded run {run_id} with {len(manifests)} clips")
    typer.echo(f"open http://{host}:{port}/?run_id={run_id}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    typer.run(main)
