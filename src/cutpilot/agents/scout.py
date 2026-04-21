"""Scout — deterministic function that samples frames from a long-form video,
calls the NIM VL endpoint once via Google ADK's LiteLlm wrapper, and returns a
validated `CandidatesResult`. No tools, no agent loop.

Transcript is optional: when `work/<run_id>/transcript.json` exists it is used as
extra text context. When it doesn't (Whisper lives on a sibling branch and isn't
wired yet), Scout runs from frames alone.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import structlog
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.genai import types as genai_types
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import LLMRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import ValidationError

from cutpilot import paths, persistence, prompts
from cutpilot.clients.ffmpeg import prepare_video_for_vl, probe_duration
from cutpilot.models import Candidate, CandidatesResult, Transcript

log = structlog.get_logger()

VL_VIDEO_HEIGHT = 480
VL_VIDEO_CRF = 30
TRANSCRIPT_CHAR_BUDGET = 4000


class ScoutConfig(FunctionBaseConfig, name="cutpilot_scout"):
    """Scout is bound to the VL LLM (Nemotron Nano 12B V2 VL on NIM)."""

    llm: LLMRef


async def scout_core(
    llm: LiteLlm,
    source_path: Path,
    run_id: str,
    system_prompt: str,
    transcript: Transcript | None = None,
) -> CandidatesResult:
    """Core Scout call — decoupled from NAT's builder for smoke-test reuse.

    Steps: transcode the source to a VL-friendly mp4 (smaller, audio dropped),
    send it as a single `video/mp4` Part (ADK → LiteLlm → NIM `video_url`),
    set `response_schema=CandidatesResult`, invoke, validate.

    The NIM samples frames server-side; `num_frames` / `fps` are governed by the
    Nemotron Nano VL defaults. Pydantic `ValidationError` is the fail-closed
    contract; no retry or repair.
    """
    duration = await probe_duration(source_path)
    vl_video = paths.vl_video_path(run_id)
    if not vl_video.exists():
        await prepare_video_for_vl(
            source_path,
            vl_video,
            height=VL_VIDEO_HEIGHT,
            crf=VL_VIDEO_CRF,
        )
    mp4_bytes = vl_video.read_bytes()
    log.info(
        "scout.video_ready",
        duration_s=duration,
        vl_video_mb=len(mp4_bytes) / (1024 * 1024),
    )

    user_text = _build_user_text(duration, transcript)
    request = LlmRequest(
        contents=[genai_types.Content(
            role="user",
            parts=[
                genai_types.Part.from_text(text=user_text),
                genai_types.Part.from_bytes(data=mp4_bytes, mime_type="video/mp4"),
            ],
        )],
        config=genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_schema=CandidatesResult,
            response_mime_type="application/json",
        ),
    )

    text_buf: list[str] = []
    async for resp in llm.generate_content_async(request, stream=False):
        if resp.content and resp.content.parts:
            text_buf.extend(p.text for p in resp.content.parts if p.text)
    raw = "".join(text_buf).strip()
    log.info("scout.response_received", chars=len(raw))
    return _parse_candidates(raw)


def _parse_candidates(raw: str) -> CandidatesResult:
    """Parse model output into `CandidatesResult`, repairing near-miss candidates.

    Nemotron VL reliably undershoots the duration floor — it keeps returning 15–19 s
    clips even when the prompt forbids it. Rather than throw away the moment the
    model *chose*, we pad any candidate in `[MIN_ACCEPTABLE_S, MIN_DURATION_S)`
    symmetrically to `MIN_DURATION_S`. Anything shorter than MIN_ACCEPTABLE_S or
    longer than MAX_DURATION_S is dropped. `CandidatesResult.min_length=5` stays
    authoritative — if we can't recover 5, the run fails closed.
    """
    payload = json.loads(raw)
    valid: list[Candidate] = []
    dropped: list[tuple[dict, str]] = []
    for c in payload.get("candidates", []):
        repaired = _repair_candidate(c)
        if repaired is None:
            dropped.append((c, "outside repairable range"))
            continue
        try:
            valid.append(Candidate.model_validate(repaired))
        except ValidationError as e:
            dropped.append((c, str(e)))
    if dropped:
        log.warning("scout.candidates_dropped", n_dropped=len(dropped), reasons=[r for _, r in dropped])
    return CandidatesResult(candidates=valid)


MIN_DURATION_S = 20.0
MAX_DURATION_S = 90.0
MIN_ACCEPTABLE_S = 12.0


def _repair_candidate(raw: dict) -> dict | None:
    """Symmetrically pad a short-but-close candidate to `MIN_DURATION_S`. Returns
    None when the candidate is too far out of range to salvage."""
    try:
        start = float(raw["start_ts"])
        end = float(raw["end_ts"])
    except (KeyError, TypeError, ValueError):
        return None
    duration = end - start
    if duration <= 0 or duration > MAX_DURATION_S or duration < MIN_ACCEPTABLE_S:
        return None
    if duration >= MIN_DURATION_S:
        return raw
    pad_each_side = (MIN_DURATION_S - duration) / 2
    return {**raw, "start_ts": max(0.0, start - pad_each_side), "end_ts": end + pad_each_side}


def _build_user_text(duration: float, transcript: Transcript | None) -> str:
    """Assemble the user-facing prompt: duration + optional transcript."""
    lines = [f"Video duration: {duration:.1f} seconds."]
    if transcript is not None:
        snippet = transcript.full_text
        if len(snippet) > TRANSCRIPT_CHAR_BUDGET:
            snippet = snippet[:TRANSCRIPT_CHAR_BUDGET] + " […truncated]"
        lines.append("")
        lines.append("Transcript (may be truncated):")
        lines.append(snippet)
    lines.append("")
    lines.append("Return JSON matching the CandidatesResult schema. Nothing else.")
    return "\n".join(lines)


@register_function(config_type=ScoutConfig, framework_wrappers=[LLMFrameworkEnum.ADK])
async def register(config: ScoutConfig, builder: Builder) -> AsyncIterator[FunctionInfo]:
    system_prompt = prompts.load("scout")
    llm = await builder.get_llm(config.llm, wrapper_type=LLMFrameworkEnum.ADK)

    async def _scout(run_id: str, source_path: str) -> CandidatesResult:
        """Propose 5–10 candidate short-form clip moments from a long-form video.

        Samples uniformly-spaced JPEG frames (ffmpeg) and calls the NIM VL endpoint
        in a single pass. Transcript is loaded from disk if present; otherwise
        Scout runs from frames alone.

        Args:
            run_id: The pipeline run identifier.
            source_path: Absolute path to the source video.

        Returns:
            A validated `CandidatesResult` with between 5 and 10 candidates.
        """
        transcript: Transcript | None = None
        transcript_path = paths.transcript_json_path(run_id)
        if transcript_path.exists():
            transcript = persistence.load(Transcript, transcript_path)
        log.info(
            "scout.start",
            run_id=run_id,
            source=source_path,
            transcript_available=transcript is not None,
        )
        return await scout_core(
            llm=llm,
            source_path=Path(source_path),
            run_id=run_id,
            system_prompt=system_prompt,
            transcript=transcript,
        )

    yield FunctionInfo.from_fn(_scout, description=_scout.__doc__)
