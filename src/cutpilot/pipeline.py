"""Non-agent pipeline orchestrator: ingest → transcribe → Scout → materialize → save.

This file holds no agent logic. Agent work is delegated via `scout_core`, and
materialization goes through `clients/ffmpeg.py`. Manifest persistence happens
at the tail.

An optional `on_stage` callback lets a frontend (the FastAPI server) observe
stage transitions without coupling the pipeline to HTTP concerns. The callback
receives a literal stage name — see `PipelineStage` below for the closed set.

For the hackathon cut we bypass the full NAT `sequential_executor` and do
deterministic top-3 selection + ffmpeg materialization in Python; the YAML
workflow at `configs/cutpilot.yml` stays in place as the `nat run` entry point
for when the Editor agent loop is known-working end-to-end.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal, TypeAlias

import structlog

from cutpilot import paths, persistence, prompts
from cutpilot.agents.scout import scout_core, scout_text_core, scout_vl_sliding
from cutpilot.clients.ffmpeg import prepare_video_for_vl
from cutpilot import paths as paths_mod  # noqa: F401 — re-used via paths above
from cutpilot.clients.ffmpeg import (
    concat_reencode,
    crop_9_16_center,
    cut_reencode,
    extract_audio,
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
) -> list[ClipManifest]:
    """End-to-end pipeline. `source` is either a local file path or an http(s) URL."""
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
) -> list[ClipManifest]:
    """Scout → deterministic top-3 → ffmpeg materialization.

    The full NAT `sequential_executor` is declared in `configs/cutpilot.yml` and
    usable via `nat run` directly; we take a shorter Python-driven path here
    because (a) sequential_executor only returns the last agent's text blob
    (not `list[ClipManifest]`), and (b) it accepts a single string input, so
    passing `(run_id, source_path)` to Scout needs a JSON-dispatcher layer we
    don't yet need.
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
    top3 = sorted(candidates.candidates, key=lambda c: c.scores.composite, reverse=True)[:3]
    for rank, cand in enumerate(top3, start=1):
        log.info(
            "pipeline.top3_selected",
            rank=rank,
            start_ts=cand.start_ts,
            end_ts=cand.end_ts,
            duration=cand.end_ts - cand.start_ts,
            composite=cand.scores.composite,
            hook=cand.hook[:80],
        )

    manifests: list[ClipManifest] = []
    for idx, cand in enumerate(top3, start=1):
        output_path = paths.clip_path(run_id, idx)
        await _materialize_clip(source, cand.start_ts, cand.end_ts, output_path)
        size_mb = output_path.stat().st_size / (1024 * 1024) if output_path.exists() else 0.0
        log.info(
            "pipeline.clip_materialized",
            clip_index=idx,
            start_ts=cand.start_ts,
            end_ts=cand.end_ts,
            composite=cand.scores.composite,
            output=str(output_path),
            size_mb=round(size_mb, 2),
        )
        manifests.append(
            ClipManifest(
                clip_index=idx,
                source_path=source,
                start_ts=cand.start_ts,
                end_ts=cand.end_ts,
                hook=cand.hook,
                rationale=cand.rationale,
                scores=cand.scores,
                caption_text="",  # captions require a transcript — wire when burn_captions lands
                output_path=output_path,
            )
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


async def _materialize_clip(source: Path, start_ts: float, end_ts: float, output: Path) -> None:
    """Cut + crop to 9:16 in a single chain. Re-encode path only (frame-accurate)."""
    cut_tmp = output.with_suffix(".cut.mp4")
    try:
        await cut_reencode(source, start_ts, end_ts, cut_tmp)
        await crop_9_16_center(cut_tmp, output)
    finally:
        cut_tmp.unlink(missing_ok=True)
