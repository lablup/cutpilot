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
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cutpilot import paths, persistence, prompts
from cutpilot.clients.ffmpeg import cut_reencode, prepare_video_for_vl, probe_duration
from cutpilot.models import Candidate, CandidatesResult, Transcript, WindowAnalysis

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

    # Persist the raw response so stale-transcript / weird-output failures are
    # inspectable after the run. The file is in work/<run_id>/, not outputs/,
    # so it's treated as a debug artifact and git-ignored.
    raw_path = paths.scout_raw_response_path(run_id)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(raw)
    log.info("scout.raw_persisted", path=str(raw_path))

    result = _parse_candidates(raw)
    # Persist the validated candidates alongside the raw response for parity.
    persistence.save(result, paths.candidates_json_path(run_id))
    for i, c in enumerate(result.candidates):
        log.info(
            "scout.candidate_accepted",
            index=i,
            start_ts=c.start_ts,
            end_ts=c.end_ts,
            duration=c.end_ts - c.start_ts,
            composite=c.scores.composite,
            hook=c.hook[:80],
        )
    log.info("scout.parse_summary", n_accepted=len(result.candidates))
    return result


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
    raw_candidates = payload.get("candidates", [])
    log.info("scout.parse_start", n_raw=len(raw_candidates))
    valid: list[Candidate] = []
    dropped: list[tuple[dict, str]] = []
    for idx, c in enumerate(raw_candidates):
        repaired = _repair_candidate(c)
        if repaired is None:
            log.warning("scout.candidate_dropped", index=idx, reason="outside_repairable_range",
                        start_ts=c.get("start_ts"), end_ts=c.get("end_ts"))
            dropped.append((c, "outside repairable range"))
            continue
        try:
            candidate = Candidate.model_validate(repaired)
            if repaired is not c:
                log.info("scout.candidate_repaired", index=idx,
                         start_before=c["start_ts"], end_before=c["end_ts"],
                         start_after=candidate.start_ts, end_after=candidate.end_ts)
            valid.append(candidate)
        except ValidationError as e:
            log.warning("scout.candidate_dropped", index=idx, reason="validation_error",
                        error=str(e)[:200])
            dropped.append((c, str(e)))
    log.info("scout.parse_done", n_accepted=len(valid), n_dropped=len(dropped))
    return CandidatesResult(candidates=valid)


MIN_DURATION_S = 20.0
MAX_DURATION_S = 90.0
MIN_ACCEPTABLE_S = 12.0


def _repair_candidate(raw: dict) -> dict | None:
    """Coerce a candidate's duration into `[MIN_DURATION_S, MAX_DURATION_S]`.
    Returns None when the candidate is too malformed or too short to salvage.

    - `duration < MIN_ACCEPTABLE_S` or non-positive: drop.
    - `duration > MAX_DURATION_S`: truncate the end so duration == MAX_DURATION_S,
      preserving Scout's chosen starting point. Nemotron VL occasionally returns
      candidates that are uniform chunks of the whole video; truncation keeps the
      starts (which carry Scout's signal) rather than dropping them.
    - `[MIN_ACCEPTABLE_S, MIN_DURATION_S)`: symmetrically pad up to
      MIN_DURATION_S. If padding would push `start_ts` below zero, push the
      leftover pad onto the end instead.
    - `[MIN_DURATION_S, MAX_DURATION_S]`: pass through unchanged.
    """
    try:
        start = float(raw["start_ts"])
        end = float(raw["end_ts"])
    except (KeyError, TypeError, ValueError):
        return None
    duration = end - start
    if duration <= 0 or duration < MIN_ACCEPTABLE_S:
        return None
    if duration > MAX_DURATION_S:
        return {**raw, "start_ts": start, "end_ts": start + MAX_DURATION_S}
    if duration >= MIN_DURATION_S:
        return raw
    need = MIN_DURATION_S - duration
    pad_left = min(start, need / 2)
    pad_right = need - pad_left
    return {**raw, "start_ts": start - pad_left, "end_ts": end + pad_right}


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


async def scout_vl_sliding(
    vl_video_path: Path,
    duration: float,
    run_id: str,
    n_windows: int = 15,
    window_len_s: float = 90.0,
    concurrency: int = 4,
) -> list[WindowAnalysis]:
    """Slide the VL NIM across the video in uniformly-spaced windows.

    The full-video VL call pattern-collapses on monotonic content (one speaker
    on stage for 43 min). A short window gives VL enough frames to actually
    distinguish — it describes what's on the slide, what gesture is being made,
    etc. We scan `n_windows` windows of `window_len_s` seconds each, spaced
    evenly across `[0, duration]` (endpoints inclusive). Parallel with a
    semaphore so we don't blast the NIM.
    """
    import asyncio
    import base64

    from openai import AsyncOpenAI

    starts = _compute_window_starts(duration, n_windows, window_len_s)

    log.info(
        "scout_vl_sliding.start",
        n_windows=len(starts),
        window_len_s=window_len_s,
        concurrency=concurrency,
        duration=duration,
    )

    sem = asyncio.Semaphore(concurrency)
    windows_dir = paths.work_dir(run_id) / "vl_windows"
    windows_dir.mkdir(parents=True, exist_ok=True)
    client = AsyncOpenAI(
        base_url=settings.nim_vl_base_url,
        api_key=settings.nvidia_api_key or "dummy",
    )

    async def _scan_one(idx: int, start: float) -> WindowAnalysis | None:
        end = min(duration, start + window_len_s)
        window_path = windows_dir / f"window_{idx:03d}.mp4"
        try:
            async with sem:
                if not window_path.exists():
                    await cut_reencode(vl_video_path, start, end, window_path)
                mp4_bytes = window_path.read_bytes()
                b64 = base64.b64encode(mp4_bytes).decode()
                response = await client.beta.chat.completions.parse(
                    model=settings.nim_vl_model,
                    messages=[
                        {"role": "system", "content":
                            "You are a social-media editor. Rate how visually "
                            "compelling a short video segment is and describe "
                            "what's visible. Score 1-5 (5 = very compelling, "
                            "1 = flat/boring). Be concise."},
                        {"role": "user", "content": [
                            {"type": "text", "text":
                                f"This is a {end - start:.0f}-second segment "
                                f"from a longer video. Rate it and describe "
                                f"what's visible."},
                            {"type": "video_url",
                             "video_url": {"url": f"data:video/mp4;base64,{b64}"}},
                        ]},
                    ],
                    response_format=_WindowAnalysisPayload,
                    temperature=0.2,
                    max_tokens=512,
                    extra_body={"media_io_kwargs":
                                {"video": {"fps": 2, "num_frames": 32}}},
                )
                parsed = response.choices[0].message.parsed
                if parsed is None:
                    log.warning("scout_vl_sliding.refused", index=idx,
                                start=start, refusal=response.choices[0].message.refusal)
                    return None
                wa = WindowAnalysis(
                    start_ts=start,
                    end_ts=end,
                    visual_score=parsed.visual_score,
                    visual_hook=parsed.visual_hook,
                )
                log.info(
                    "scout_vl_sliding.window_scored",
                    index=idx,
                    start=round(start, 1),
                    end=round(end, 1),
                    visual_score=wa.visual_score,
                    visual_hook=wa.visual_hook[:60],
                )
                return wa
        except Exception as exc:  # noqa: BLE001 — per-window failure should not kill the scan
            log.warning(
                "scout_vl_sliding.window_failed",
                index=idx,
                start=round(start, 1),
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )
            return None

    results = await asyncio.gather(
        *(_scan_one(i, s) for i, s in enumerate(starts)),
    )
    windows = [w for w in results if w is not None]
    log.info("scout_vl_sliding.done", n_scored=len(windows), n_requested=len(starts))
    return windows


class _WindowAnalysisPayload(BaseModel):
    """The subset of WindowAnalysis the VL NIM fills in (start/end are known
    client-side, so we don't round-trip them)."""

    model_config = ConfigDict(extra="forbid")

    visual_score: int = Field(ge=1, le=5)
    visual_hook: str


def _compute_window_starts(
    duration: float,
    n_windows: int,
    window_len_s: float,
) -> list[float]:
    """Return uniformly-spaced start times covering `[0, duration - window_len_s]`.

    Rules:
    - If the source is shorter than one window, return `[0.0]` and let the
      scan read a single partial window.
    - If `n_windows == 1`, the single window starts at 0.
    - Otherwise, `n_windows` starts are placed with uniform stride, first at
      `0.0` and last at `duration - window_len_s` (endpoints inclusive).
    """
    if duration <= window_len_s or n_windows <= 1:
        return [0.0]
    stride = (duration - window_len_s) / (n_windows - 1)
    return [i * stride for i in range(n_windows)]


async def scout_text_core(
    transcript: Transcript,
    run_id: str,
    system_prompt: str,
    windows: list[WindowAnalysis] | None = None,
) -> CandidatesResult:
    """Text-LLM-driven candidate selection from the Whisper transcript.

    The VL model (Scout) visually pattern-collapses on monotonic talks (one
    speaker on stage). The text NIM reads the actual *content* and picks
    semantically-varied moments — which is what a human would do.

    Uses the openai SDK's `beta.chat.completions.parse(response_format=<Pydantic>)`
    helper: it walks the Pydantic class, inlines `$defs`, adds
    `additionalProperties: false`, and flattens `required` — producing a
    schema NIM's strict mode accepts — then parses the response back into a
    validated `CandidatesResult` instance via `.parsed`. Raw Pydantic
    `model_json_schema()` wouldn't work directly because `CandidatesResult`
    has nested `$defs` (Candidate, RubricScores) that strict mode rejects.

    Fallback: if the NIM emits JSON that passes the json-schema but fails our
    `@model_validator` (e.g., 10-second or 130-second candidates — Nemotron
    does this reliably), `.parsed` is None and `message.content` holds the
    raw JSON. We route it through `_parse_candidates`, which repairs the
    near-misses and drops the unsalvageable.
    """
    from openai import AsyncOpenAI  # local import: text_scout is optional

    client = AsyncOpenAI(
        base_url=settings.nim_text_base_url,
        api_key=settings.nvidia_api_key or "dummy",
    )
    user_text = _build_transcript_prompt(transcript, windows=windows)
    log.info(
        "scout_text.start",
        base_url=settings.nim_text_base_url,
        model=settings.nim_text_model,
        transcript_chars=len(user_text),
        n_vl_windows=len(windows) if windows else 0,
    )

    response = await client.beta.chat.completions.parse(
        model=settings.nim_text_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        response_format=CandidatesResult,
        temperature=0.2,
        max_tokens=2048,
    )
    message = response.choices[0].message
    raw = message.content or ""
    refusal = message.refusal or ""
    log.info(
        "scout_text.response_received",
        chars=len(raw),
        refusal=refusal[:200] if refusal else None,
        parsed_ok=message.parsed is not None,
    )

    raw_path = paths.scout_raw_response_path(run_id)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(raw)
    log.info("scout_text.raw_persisted", path=str(raw_path))

    if message.parsed is not None:
        # NIM emitted schema-valid AND model-validator-valid output. Straight
        # through — `.parsed` is already a `CandidatesResult` instance.
        result = message.parsed
    else:
        # NIM refused, or passed json-schema but failed Pydantic @model_validator
        # on one or more candidates. Repair what we can from the raw text.
        log.warning("scout_text.repair_fallback", refusal=bool(refusal))
        result = _parse_candidates(raw)

    persistence.save(result, paths.candidates_json_path(run_id))
    for i, c in enumerate(result.candidates):
        log.info(
            "scout_text.candidate_accepted",
            index=i,
            start_ts=c.start_ts,
            end_ts=c.end_ts,
            duration=c.end_ts - c.start_ts,
            composite=c.scores.composite,
            hook=c.hook[:80],
        )
    return result


def _build_transcript_prompt(
    transcript: Transcript,
    windows: list[WindowAnalysis] | None = None,
) -> str:
    """Render the transcript + optional per-window VL observations so the text
    LLM can reason over both audio-content AND visual-evidence timelines."""
    lines = [f"Video duration: {transcript.duration:.1f} seconds.", ""]
    lines.append("Transcript (segment timestamps in seconds):")
    for seg in transcript.segments:
        text = seg.text.strip()
        if not text:
            continue
        lines.append(f"[{seg.start:.1f}-{seg.end:.1f}] {text}")
    if windows:
        lines.append("")
        lines.append(
            "Visual observations from a sliding-window scan of the video "
            "(each window independently scored 1-5 for visual appeal):"
        )
        for w in windows:
            lines.append(
                f"[{w.start_ts:.1f}-{w.end_ts:.1f}] visual_score={w.visual_score} "
                f"| {w.visual_hook}"
            )
    lines.append("")
    lines.append(
        "Pick 5–10 candidate clips. Each must cover a DIFFERENT topic or moment "
        "— do not return near-identical candidates. When picking, favor moments "
        "where the transcript content AND a high visual_score align. Set each "
        "candidate's `scores.visual_fit` using the visual_score of the window(s) "
        "your candidate overlaps. Return JSON matching the CandidatesResult "
        "schema, nothing else."
    )
    return "\n".join(lines)


from cutpilot.settings import settings  # noqa: E402 — used by scout_text_core only


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
