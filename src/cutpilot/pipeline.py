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
from cutpilot.clients.whisper_riva import transcribe
from cutpilot.models import ClipManifest, Transcript

log = structlog.get_logger()


async def run_pipeline(source: Path, run_id: str) -> list[ClipManifest]:
    """End-to-end pipeline. Returns the three clip manifests."""
    paths.ensure_dirs(run_id)

    # 1. Ingest — demux audio
    wav_path = paths.audio_wav_path(run_id)
    await extract_audio(source, wav_path)
    log.info("pipeline.audio_extracted", path=str(wav_path))

    # 2. Perception — Whisper-Large via Riva
    transcript: Transcript = await transcribe(audio_path=wav_path, source_path=source)
    persistence.save(transcript, paths.transcript_json_path(run_id))
    log.info("pipeline.transcribed", segments=len(transcript.segments))

    # 3. Agent workflow — Scout (VL NIM) → Editor (text NIM + tools)
    manifests = await _run_nat_workflow(source=source, transcript=transcript, run_id=run_id)
    log.info("pipeline.done", clips=len(manifests))

    # 4. Save manifests
    for m in manifests:
        persistence.save(m, paths.clip_manifest_path(run_id, m.clip_index))

    return manifests


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
