"""FastAPI HTTP façade for CutPilot.

Thin wrapper around `pipeline.run_pipeline`:

- `POST /runs`        — submit a source (URL or path), returns a run_id, kicks
                        the pipeline off in a BackgroundTask.
- `GET  /runs/{id}`   — current stage / status / error / manifests.
- `GET  /outputs/...` — static mount over the on-disk outputs dir so the UI
                        can load clip mp4s directly as `<video src=...>`.
- `GET  /`            — static mount over the `ui/` directory.

State is an in-memory `dict[run_id, RunState]`. Fine for a single-worker,
single-user hackathon demo; runs are lost on restart. Scaling past that would
mean a shared store (SQLite / Redis) and a real task queue — out of scope.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import IO, AsyncIterator

import structlog
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cutpilot import paths
from cutpilot.models import ClipManifest
from cutpilot.pipeline import PipelineStage, run_pipeline

log = structlog.get_logger()


class RunStatus(str, Enum):
    """Superset of `PipelineStage`: adds the lifecycle states the pipeline
    itself doesn't own (initial `pending`, terminal `done` / `failed`)."""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    SCOUTING = "scouting"
    EDITING = "editing"
    DONE = "done"
    FAILED = "failed"


class RunState(BaseModel):
    run_id: str
    status: RunStatus
    source: str
    created_at: datetime
    error: str | None = None
    manifests: list[ClipManifest] = Field(default_factory=list)


class CreateRunRequest(BaseModel):
    source: str = Field(min_length=1)


_RUNS: dict[str, RunState] = {}


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    paths.outputs_root().mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="CutPilot", lifespan=_lifespan)


@app.post("/runs", response_model=RunState)
async def create_run(
    request: CreateRunRequest,
    background_tasks: BackgroundTasks,
) -> RunState:
    run_id = uuid.uuid4().hex[:8]
    state = RunState(
        run_id=run_id,
        status=RunStatus.PENDING,
        source=request.source,
        created_at=datetime.now(UTC),
    )
    _RUNS[run_id] = state
    background_tasks.add_task(_execute_run, run_id)
    log.info("server.run.submitted", run_id=run_id, source=request.source)
    return state


@app.post("/runs/upload", response_model=RunState)
async def create_run_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> RunState:
    """Multipart alternative to `POST /runs`: save the upload to disk, then
    kick the pipeline off with its local path as `source`."""
    run_id = uuid.uuid4().hex[:8]
    paths.ensure_dirs(run_id)

    original_name = file.filename or "upload.mp4"
    extension = Path(original_name).suffix or ".mp4"
    target = paths.uploaded_source_path(run_id, extension)
    await _save_upload(upload=file, target=target)

    state = RunState(
        run_id=run_id,
        status=RunStatus.PENDING,
        source=str(target),
        created_at=datetime.now(UTC),
    )
    _RUNS[run_id] = state
    background_tasks.add_task(_execute_run, run_id)
    log.info(
        "server.run.uploaded",
        run_id=run_id,
        filename=original_name,
        target=str(target),
    )
    return state


@app.get("/runs/{run_id}", response_model=RunState)
async def get_run(run_id: str) -> RunState:
    state = _RUNS.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run_id {run_id!r} not found")
    return state


async def _save_upload(*, upload: UploadFile, target: Path) -> None:
    """Copy an `UploadFile` to disk off the event loop.

    `UploadFile.file` is a `SpooledTemporaryFile`; `shutil.copyfileobj` streams
    through it without loading the whole video into memory. The copy runs in
    a worker thread so multi-GB uploads don't stall other requests."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        await asyncio.to_thread(_copy_upload_sync, upload.file, target)
    finally:
        await upload.close()


def _copy_upload_sync(source_stream: IO[bytes], target: Path) -> None:
    with target.open("wb") as out:
        shutil.copyfileobj(source_stream, out)


async def _execute_run(run_id: str) -> None:
    """BackgroundTask body. Owns the run's lifecycle transitions — everything
    except `DOWNLOADING / TRANSCRIBING / SCOUTING / EDITING`, which come from
    the pipeline's `on_stage` callback."""
    state = _RUNS[run_id]

    def on_stage(stage: PipelineStage) -> None:
        state.status = RunStatus(stage)

    try:
        manifests = await run_pipeline(
            source=state.source,
            run_id=run_id,
            on_stage=on_stage,
        )
    except Exception as exc:  # noqa: BLE001 — any failure should land in state
        log.exception("server.run.failed", run_id=run_id)
        state.status = RunStatus.FAILED
        state.error = f"{type(exc).__name__}: {exc}"
        return

    state.manifests = manifests
    state.status = RunStatus.DONE


# Static mounts. Order matters — more specific prefixes first; the root mount
# must be last so the routes above aren't shadowed.
app.mount(
    "/outputs",
    StaticFiles(directory=str(paths.outputs_root())),
    name="outputs",
)
app.mount(
    "/",
    StaticFiles(directory=str(paths.ui_dir()), html=True),
    name="ui",
)
