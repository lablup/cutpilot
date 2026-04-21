"""Non-agent pipeline orchestrator: ingest → transcribe → Scout → materialize → save.

This is the only place that combines perception, Scout, and manifest persistence.
For the hackathon cut we bypass the full NAT `sequential_executor` and do
deterministic top-3 selection + ffmpeg materialization in Python; the YAML
workflow at `configs/cutpilot.yml` stays in place as the `nat run` entry point
for when the Editor agent loop is known-working end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from cutpilot import paths, persistence, prompts
from cutpilot.agents.scout import scout_core
from cutpilot.clients.ffmpeg import crop_9_16_center, cut_reencode, extract_audio
from cutpilot.clients.nim import make_vl_llm
from cutpilot.clients.whisper import transcribe
from cutpilot.models import CandidatesResult, ClipManifest, Transcript
from cutpilot.settings import settings

log = structlog.get_logger()


async def run_pipeline(source: Path, run_id: str) -> list[ClipManifest]:
    """End-to-end pipeline. Returns the three clip manifests."""
    paths.ensure_dirs(run_id)

    wav_path = paths.audio_wav_path(run_id)
    await extract_audio(source, wav_path)
    log.info("pipeline.audio_extracted", path=str(wav_path))

    transcript = await _transcribe_or_empty(wav_path, source)
    persistence.save(transcript, paths.transcript_json_path(run_id))
    log.info("pipeline.transcribed", segments=len(transcript.segments))

    manifests = await _run_nat_workflow(source=source, transcript=transcript, run_id=run_id)
    log.info("pipeline.done", clips=len(manifests))

    for m in manifests:
        persistence.save(m, paths.clip_manifest_path(run_id, m.clip_index))

    return manifests


async def _transcribe_or_empty(wav_path: Path, source: Path) -> Transcript:
    """Whisper transcription with graceful degradation.

    Whisper's NIM endpoint may be unreachable (Cloudflare tunnel cycling, friend's
    branch not yet merged, etc). Scout accepts an absent transcript, so the pipeline
    downgrades to an empty `Transcript` rather than blocking the full run.
    """
    try:
        return await transcribe(audio_path=wav_path, source_path=source)
    except Exception as exc:  # noqa: BLE001 — any failure downgrades, not just network
        log.warning("pipeline.whisper_failed", error=str(exc), error_type=type(exc).__name__)
        return Transcript(
            source_path=source,
            language=settings.whisper_language,
            duration=0.0,
            segments=[],
        )


async def _run_nat_workflow(
    source: Path,
    transcript: Transcript,
    run_id: str,
) -> list[ClipManifest]:
    """Scout → deterministic top-3 → ffmpeg materialization.

    The full NAT `sequential_executor` is declared in `configs/cutpilot.yml` and
    usable via `nat run` directly; we take a shorter Python-driven path here
    because (a) sequential_executor only returns the last agent's text blob
    (not `list[ClipManifest]`), and (b) it accepts a single string input, so
    passing `(run_id, source_path)` to Scout needs a JSON-dispatcher layer we
    don't yet need.
    """
    llm = make_vl_llm()
    candidates: CandidatesResult = await scout_core(
        llm=llm,
        source_path=source,
        run_id=run_id,
        system_prompt=prompts.load("scout"),
        transcript=transcript if transcript.segments else None,
    )
    log.info("pipeline.scout_done", n_candidates=len(candidates.candidates))

    top3 = sorted(candidates.candidates, key=lambda c: c.scores.composite, reverse=True)[:3]

    manifests: list[ClipManifest] = []
    for idx, cand in enumerate(top3, start=1):
        output_path = paths.clip_path(run_id, idx)
        await _materialize_clip(source, cand.start_ts, cand.end_ts, output_path)
        log.info(
            "pipeline.clip_materialized",
            clip_index=idx,
            start_ts=cand.start_ts,
            end_ts=cand.end_ts,
            composite=cand.scores.composite,
            output=str(output_path),
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
                caption_text="",  # captions require a transcript — wire when Whisper lands
                output_path=output_path,
            )
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
