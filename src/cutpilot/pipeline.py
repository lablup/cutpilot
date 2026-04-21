"""Non-agent pipeline orchestrator: ingest → transcribe → run_workflow → save.

This file holds no agent logic. Agent work is delegated to the NAT workflow loaded
from `configs/cutpilot.yml`. This is the only place that combines perception,
NAT workflow invocation, and manifest persistence.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from cutpilot import paths, persistence
from cutpilot.clients.ffmpeg import extract_audio
from cutpilot.clients.whisper import transcribe
from cutpilot.clients.youtube import download, is_url
from cutpilot.models import ClipManifest, Transcript

log = structlog.get_logger()


class SourceNotFoundError(FileNotFoundError):
    """Raised when a local source path does not resolve to a readable file."""


async def run_pipeline(source: str, run_id: str) -> list[ClipManifest]:
    """End-to-end pipeline. `source` is either a local file path or an http(s) URL."""
    paths.ensure_dirs(run_id)

    # 0. Ingest — resolve the CLI argument into a concrete local file
    local_source = await _resolve_source(source=source, run_id=run_id)
    log.info("pipeline.source_resolved", path=str(local_source))

    # 1. Demux audio
    wav_path = paths.audio_wav_path(run_id)
    await extract_audio(local_source, wav_path)
    log.info("pipeline.audio_extracted", path=str(wav_path))

    # 2. Perception — Whisper-Large via NIM (OpenAI-compat /v1/audio/transcriptions)
    transcript: Transcript = await transcribe(
        audio_path=wav_path,
        source_path=local_source,
        chunks_dir=paths.whisper_chunks_dir(run_id),
    )
    persistence.save(transcript, paths.transcript_json_path(run_id))
    log.info("pipeline.transcribed", segments=len(transcript.segments))

    # 3. Agent workflow — Scout (VL NIM) → Editor (text NIM + tools)
    manifests = await _run_nat_workflow(
        source=local_source,
        transcript=transcript,
        run_id=run_id,
    )
    log.info("pipeline.done", clips=len(manifests))

    # 4. Save manifests
    for manifest in manifests:
        persistence.save(manifest, paths.clip_manifest_path(run_id, manifest.clip_index))

    return manifests


async def _resolve_source(*, source: str, run_id: str) -> Path:
    """Turn a CLI source string into an on-disk video path.

    - `http(s)://...` is fetched into the run's work dir via yt-dlp.
    - Everything else is treated as a local filesystem path.
    """
    if is_url(source):
        target = paths.source_video_path(run_id)
        return await download(url=source, target_path=target)

    local = Path(source).expanduser()
    if not local.exists():
        raise SourceNotFoundError(f"Source path does not exist: {local}")
    if not local.is_file():
        raise SourceNotFoundError(f"Source path is not a file: {local}")
    return local.resolve()


async def _run_nat_workflow(
    source: Path,
    transcript: Transcript,
    run_id: str,
) -> list[ClipManifest]:
    """Load and invoke the NAT workflow from `configs/cutpilot.yml`.

    TODO: wire up `nat.runtime.load_workflow(...)` once tool and scout registrations
    are exercised end-to-end. The workflow returns the three-clip plan; materialization
    (ffmpeg calls) happens inside the Editor's tool calls and yields file paths we read
    back into `ClipManifest` objects here.
    """
    raise NotImplementedError("Wire to nat.runtime once tool registrations are green.")
