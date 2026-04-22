"""Pipeline orchestrator: ingest → transcribe → Scout → Editor → stitch → save.

Agent work is delegated: Scout (`agents/scout.py`) picks candidates via the
VL+text NIMs; Editor (`agents/editor.py`) runs an OpenAI-tool-calling loop
on the text NIM to refine boundaries and materialize the 3 clips via
`cut → crop_9_16 → burn_captions`. ffmpeg subprocess work is owned by
`clients/ffmpeg.py`; this module only wires stages together.

An optional `on_stage` callback lets a frontend (the FastAPI server) observe
stage transitions without coupling the pipeline to HTTP concerns. The callback
receives a literal stage name — see `PipelineStage` below for the closed set.

The declarative NAT `sequential_executor` workflow at `configs/cutpilot.yml`
is the `nat run` entrypoint and wires Scout → Editor too, but the CLI drives
this Python path because it needs `list[ClipManifest]` out (not a text blob)
and accepts a `(run_id, source_path)` pair (not a single string input).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal, TypeAlias

import structlog

from cutpilot import paths, persistence, prompts
from cutpilot.agents.editor import editor_core
from cutpilot.agents.scout import scout_core, scout_text_core, scout_vl_sliding
from cutpilot.clients.ffmpeg import (
    concat_reencode,
    extract_audio,
    prepare_video_for_vl,
)
from cutpilot.clients.nim import make_vl_llm
from cutpilot.clients.whisper import transcribe
from cutpilot.clients.youtube import download, is_url
from cutpilot.models import CandidatesResult, ClipManifest, Transcript

log = structlog.get_logger()

PipelineStage: TypeAlias = Literal[
    "downloading",
    "transcribing",
    "scouting",
    "editing",
]
StageCallback: TypeAlias = Callable[[PipelineStage], None] | None


class SourceNotFoundError(FileNotFoundError):
    """Raised when a local source path does not resolve to a readable file."""


async def run_pipeline(
    source: str,
    run_id: str,
    on_stage: StageCallback = None,
    *,
    burn_captions: bool = False,
) -> list[ClipManifest]:
    """End-to-end pipeline. `source` is either a local file path or an http(s) URL.

    `burn_captions` is off by default — caption text is always persisted in the
    manifest, but pixel-burning onto the video only happens when explicitly
    opted in (CLI `--burn-captions`, UI checkbox).
    """
    paths.ensure_dirs(run_id)

    # 0. Ingest — resolve the CLI argument into a concrete local file
    _emit(on_stage, "downloading")
    local_source = await _resolve_source(source=source, run_id=run_id)
    log.info("pipeline.source_resolved", path=str(local_source))

    # 1. Demux audio
    wav_path = paths.audio_wav_path(run_id)
    await extract_audio(local_source, wav_path)
    log.info("pipeline.audio_extracted", path=str(wav_path))

    # 2. Perception — Whisper-Large via NIM (OpenAI-compat /v1/audio/transcriptions)
    _emit(on_stage, "transcribing")
    transcript: Transcript = await transcribe(
        audio_path=wav_path,
        source_path=local_source,
        chunks_dir=paths.whisper_chunks_dir(run_id),
    )
    persistence.save(transcript, paths.transcript_json_path(run_id))
    log.info("pipeline.transcribed", segments=len(transcript.segments))

    # 3. Agent workflow — Scout (VL NIM) → deterministic top-3 → ffmpeg materialization
    _emit(on_stage, "scouting")
    manifests = await _run_nat_workflow(
        source=local_source,
        transcript=transcript,
        run_id=run_id,
        on_stage=on_stage,
        burn_captions=burn_captions,
    )
    log.info("pipeline.done", clips=len(manifests))

    # 4. Save manifests
    for manifest in manifests:
        persistence.save(manifest, paths.clip_manifest_path(run_id, manifest.clip_index))

    return manifests


def _emit(on_stage: StageCallback, stage: PipelineStage) -> None:
    """Swallow callback errors — observability must never break the pipeline."""
    if on_stage is None:
        return
    try:
        on_stage(stage)
    except Exception:
        log.exception("pipeline.on_stage_failed", stage=stage)


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
    on_stage: StageCallback = None,
    *,
    burn_captions: bool = False,
) -> list[ClipManifest]:
    """Scout (VL sliding + text NIM) → Editor (tool-calling) → stitch.

    Every content-bearing decision is AI-made: VL NIM scores the 15 windows,
    text NIM picks 5–10 candidates, Editor (text NIM + tool calls) refines
    boundaries via `transcript_window` and invokes `cut → crop_9_16 →
    burn_captions` for each clip. The final stitch is deterministic.
    """
    # Three-NIM architecture (matches PRD):
    #   1. Whisper → transcript (already done)
    #   2. VL NIM sliding-window scan → per-window visual scores + hooks
    #   3. Text NIM reads transcript + window scores → picks candidates
    # VL pattern-collapses on full long videos but is fine on short windows, so
    # we slide a short window across the source instead of one big call.
    vl_video = paths.vl_video_path(run_id)
    if not vl_video.exists():
        await prepare_video_for_vl(source, vl_video)

    windows = await scout_vl_sliding(
        vl_video_path=vl_video,
        duration=transcript.duration,
        run_id=run_id,
    )
    log.info(
        "pipeline.vl_scan_done",
        n_windows=len(windows),
        mean_visual_score=(
            round(sum(w.visual_score for w in windows) / len(windows), 2)
            if windows else None
        ),
    )

    if transcript.segments:
        log.info(
            "pipeline.scout_input",
            strategy="text_nim+vl_windows",
            transcript_segments=len(transcript.segments),
            transcript_duration=transcript.duration,
            n_windows=len(windows),
        )
        candidates: CandidatesResult = await scout_text_core(
            transcript=transcript,
            run_id=run_id,
            system_prompt=prompts.load("scout"),
            windows=windows,
        )
    else:
        log.info("pipeline.scout_input", strategy="vl_nim_fallback")
        llm = make_vl_llm()
        candidates = await scout_core(
            llm=llm,
            source_path=source,
            run_id=run_id,
            system_prompt=prompts.load("scout"),
            transcript=None,
        )
    log.info("pipeline.scout_done", n_candidates=len(candidates.candidates))

    _emit(on_stage, "editing")
    # Editor agent — AI-driven materialization. Picks top-3, refines boundaries
    # via transcript_window, and calls cut → crop_9_16 → burn_captions per clip.
    manifests = await editor_core(
        candidates=candidates,
        source=source,
        transcript=transcript,
        run_id=run_id,
        system_prompt=prompts.load("editor"),
        burn_captions=burn_captions,
    )
    for m in manifests:
        log.info(
            "pipeline.clip_materialized",
            clip_index=m.clip_index,
            start_ts=m.start_ts,
            end_ts=m.end_ts,
            composite=m.scores.composite,
            output=str(m.output_path),
            size_mb=round(m.output_path.stat().st_size / (1024 * 1024), 2)
                    if m.output_path.exists() else 0.0,
        )

    # Stitch: concatenate the 3 clips in chronological order into one highlight
    # reel at outputs/<run_id>/highlights.mp4. concat_reencode handles codec
    # drift and produces a single playable mp4 regardless of per-clip encode
    # variance.
    chronological = sorted(manifests, key=lambda m: m.start_ts)
    highlights_path = paths.highlights_path(run_id)
    await concat_reencode([m.output_path for m in chronological], highlights_path)
    highlights_mb = highlights_path.stat().st_size / (1024 * 1024)
    log.info(
        "pipeline.highlights_stitched",
        output=str(highlights_path),
        clips_joined=len(chronological),
        size_mb=round(highlights_mb, 2),
    )

    return manifests


