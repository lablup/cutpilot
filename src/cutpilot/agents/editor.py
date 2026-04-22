"""Editor — AI agent that materializes Scout's top-3 candidates.

The Editor is the second agent in the CutPilot pipeline. It receives Scout's
5–10 candidates, picks the top 3 by composite rubric score, then emits a
structured `EditPlan` describing how to materialize each clip. The server
dispatches each step of the plan to `clients/ffmpeg.py`.

Why a structured plan instead of OpenAI tool-calling: vLLM's `tool_choice:
auto` requires `--enable-auto-tool-choice --tool-call-parser <parser>` at
NIM startup, which the current deployed NIMs don't have. Structured output
via `beta.chat.completions.parse(response_format=EditPlan)` gives us the
same semantics (AI decides strategy + boundaries per clip; server executes)
with the API surface the NIM actually supports — same one Scout's text call
already uses.

Per clip the Editor chooses one of two strategies:
  - `cut`    → one contiguous range (starts=[s], ends=[e])
  - `splice` → 2–5 ranges concatenated (starts=[s1,s2,...], ends=[e1,e2,...])
followed by deterministic `crop_9_16 → burn_captions`. Paths, SRT
generation, and tool execution are server-side; the LLM never sees a file
path.

The plan is persisted as `outputs/<run_id>/reasoning_trace.jsonl` alongside
each executed step's return status.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import structlog
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from cutpilot import paths
from cutpilot.clients.ffmpeg import (
    burn_captions as ffmpeg_burn_captions,
    concat_reencode,
    crop_9_16_center,
    cut_reencode,
)
from cutpilot.models import (
    Candidate,
    CandidatesResult,
    ClipManifest,
    Transcript,
)
from cutpilot.settings import settings

log = structlog.get_logger()

TRANSCRIPT_CHAR_BUDGET = 8000


class _TimeRange(BaseModel):
    """A single (start, end) span on the source timeline."""

    model_config = ConfigDict(extra="forbid")

    start_ts: float = Field(ge=0)
    end_ts: float


# ---------------------------------------------------------------------------
# Edit plan — the structured output the Editor LLM emits per run. Server
# side executes each ClipEdit in order.
# ---------------------------------------------------------------------------


class ClipEdit(BaseModel):
    """One clip's materialization plan. `strategy=cut` → 1 range; `strategy=splice`
    → 2–5 ranges that will be concatenated. Boundaries are what the Editor
    decided (may differ from Scout's candidate by up to ±1 s for sentence
    alignment, or be drawn from elsewhere in the source for splice)."""

    model_config = ConfigDict(extra="forbid")

    clip_index: int = Field(ge=1, le=3)
    strategy: Literal["cut", "splice"]
    ranges: list[_TimeRange] = Field(min_length=1, max_length=5)


class EditPlan(BaseModel):
    """The Editor's full response. Exactly 3 ClipEdits, one per clip_index."""

    model_config = ConfigDict(extra="forbid")

    clips: list[ClipEdit] = Field(min_length=3, max_length=3)


class _ClipState(BaseModel):
    """Server-side bookkeeping for a single clip as the Editor materializes it.

    `ranges` is a list with one entry for plain `cut` tool calls, or several
    entries for a `splice` tool call. The manifest's `start_ts/end_ts` are
    derived from min/max of the ranges; the full splice plan lives in
    `reasoning_trace.jsonl`."""

    model_config = ConfigDict(extra="forbid")

    clip_index: int = Field(ge=1, le=3)
    candidate: Candidate
    ranges: list[_TimeRange] = Field(default_factory=list)
    cut_path: Path | None = None
    cropped_path: Path | None = None
    final_path: Path | None = None
    caption_text: str = ""

    @property
    def start_ts(self) -> float:
        return min(r.start_ts for r in self.ranges) if self.ranges else self.candidate.start_ts

    @property
    def end_ts(self) -> float:
        return max(r.end_ts for r in self.ranges) if self.ranges else self.candidate.end_ts


async def editor_core(
    candidates: CandidatesResult,
    source: Path,
    transcript: Transcript,
    run_id: str,
    system_prompt: str,
    *,
    burn_captions: bool = False,
) -> list[ClipManifest]:
    """Run the Editor agent: pick top 3, refine boundaries, materialize clips.

    Args:
        candidates: All of Scout's proposals (5–10).
        source: Local path to the source video file.
        transcript: Whisper transcript for boundary refinement and captions.
        run_id: Pipeline run id — determines output paths.
        system_prompt: Loaded from `prompts/editor.md`.

    Returns:
        Exactly 3 validated ClipManifests in clip_index order.
    """
    top3 = sorted(candidates.candidates, key=lambda c: c.scores.composite, reverse=True)[:3]
    states: dict[int, _ClipState] = {
        i: _ClipState(clip_index=i, candidate=c)
        for i, c in enumerate(top3, start=1)
    }
    log.info(
        "editor.start",
        n_clips=len(states),
        proposed_windows=[
            (s.clip_index, s.candidate.start_ts, s.candidate.end_ts)
            for s in states.values()
        ],
    )

    plan = await _request_edit_plan(top3, transcript, run_id, system_prompt)
    trace: list[dict[str, Any]] = [{"event": "edit_plan", "plan": plan.model_dump()}]
    await _execute_plan(
        plan=plan,
        states=states,
        source=source,
        transcript=transcript,
        run_id=run_id,
        trace=trace,
        burn_captions=burn_captions,
    )

    for clip_index, state in sorted(states.items()):
        if state.final_path is None or not state.final_path.exists():
            log.warning(
                "editor.clip_not_finalized",
                clip_index=clip_index,
                cut=state.cut_path and state.cut_path.exists(),
                cropped=state.cropped_path and state.cropped_path.exists(),
            )
            # Server-side backstop: finish whatever the plan skipped so the
            # pipeline always emits 3 clips. We never invent content — only
            # run the ffmpeg steps the plan didn't get to, using Scout's
            # candidate boundaries as the fallback.
            await _finish_skipped(state, source, transcript, run_id, burn_captions=burn_captions)

    _persist_trace(run_id, trace)

    return [
        ClipManifest(
            clip_index=state.clip_index,
            source_path=source,
            start_ts=state.start_ts,
            end_ts=state.end_ts,
            hook=state.candidate.hook,
            rationale=state.candidate.rationale,
            scores=state.candidate.scores,
            caption_text=state.caption_text,
            output_path=state.final_path,
            reasoning_trace_path=paths.reasoning_trace_path(run_id),
        )
        for _, state in sorted(states.items())
    ]


async def _request_edit_plan(
    top3: list[Candidate],
    transcript: Transcript,
    run_id: str,
    system_prompt: str,
) -> EditPlan:
    """Ask the text NIM for an `EditPlan` via strict-mode structured output.

    Same pattern Scout's text call uses — `beta.chat.completions.parse`
    with `response_format=EditPlan`. OpenAI SDK inlines `$defs`, sets
    `additionalProperties: false`, flattens `required` — producing a schema
    vLLM's strict mode accepts.
    """
    client = AsyncOpenAI(
        base_url=settings.nim_text_base_url,
        api_key=settings.nvidia_api_key or "dummy",
    )
    user_prompt = _build_editor_user_prompt(top3, transcript, run_id)
    log.info(
        "editor.plan_request",
        base_url=settings.nim_text_base_url,
        model=settings.nim_text_model,
        prompt_chars=len(user_prompt),
    )
    response = await client.beta.chat.completions.parse(
        model=settings.nim_text_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format=EditPlan,
        temperature=0.1,
        max_tokens=2048,
    )
    message = response.choices[0].message
    log.info(
        "editor.plan_received",
        chars=len(message.content or ""),
        refusal=(message.refusal or "")[:200] or None,
        parsed_ok=message.parsed is not None,
    )
    if message.parsed is not None:
        return message.parsed
    # Strict-schema JSON but failed model validation (rare). Parse raw as fallback.
    raw = message.content or ""
    log.warning("editor.plan_repair_fallback", raw_preview=raw[:200])
    return EditPlan.model_validate_json(raw)


async def _execute_plan(
    *,
    plan: EditPlan,
    states: dict[int, _ClipState],
    source: Path,
    transcript: Transcript,
    run_id: str,
    trace: list[dict[str, Any]],
    burn_captions: bool,
) -> None:
    """Walk the Editor's plan and invoke the underlying ffmpeg tools per clip.

    Execution is `cut | splice → crop_9_16 → burn_captions`, enforced
    server-side so the Editor can't skip steps or emit them out of order.
    `burn_captions=False` skips the pixel burn (caption text still recorded
    in the manifest for downstream use).
    """
    for clip_edit in plan.clips:
        if clip_edit.clip_index not in states:
            log.warning("editor.plan_unknown_clip_index", clip_index=clip_edit.clip_index)
            continue
        state = states[clip_edit.clip_index]

        if clip_edit.strategy == "splice" and len(clip_edit.ranges) < 2:
            log.warning(
                "editor.plan_splice_too_few_ranges",
                clip_index=clip_edit.clip_index,
                n_ranges=len(clip_edit.ranges),
            )
            # Degrade to cut semantics — use the first range only.
            clip_edit = clip_edit.model_copy(update={"strategy": "cut"})

        if clip_edit.strategy == "cut":
            args = {
                "clip_index": clip_edit.clip_index,
                "start_ts": clip_edit.ranges[0].start_ts,
                "end_ts": clip_edit.ranges[0].end_ts,
            }
            res = await _tool_cut(source, run_id, states, args)
        else:
            args = {
                "clip_index": clip_edit.clip_index,
                "ranges": [r.model_dump() for r in clip_edit.ranges],
            }
            res = await _tool_splice(source, run_id, states, args)
        trace.append({"tool": clip_edit.strategy, "args": args, "result": res})
        log.info("editor.step", tool=clip_edit.strategy, clip_index=clip_edit.clip_index, result=res)
        if res.startswith("ERROR"):
            continue

        crop_res = await _tool_crop(run_id, states, {"clip_index": clip_edit.clip_index})
        trace.append({"tool": "crop_9_16", "args": {"clip_index": clip_edit.clip_index},
                      "result": crop_res})
        log.info("editor.step", tool="crop_9_16", clip_index=clip_edit.clip_index, result=crop_res)
        if crop_res.startswith("ERROR"):
            continue

        burn_res = await _tool_burn_captions(
            transcript, run_id, states, {"clip_index": clip_edit.clip_index},
            burn=burn_captions,
        )
        trace.append({"tool": "burn_captions", "args": {"clip_index": clip_edit.clip_index},
                      "result": burn_res})
        log.info("editor.step", tool="burn_captions",
                 clip_index=clip_edit.clip_index, result=burn_res)


# ---------------------------------------------------------------------------
# Server-side tool implementations — invoked by `_execute_plan` one per
# ClipEdit in the Editor's plan.
# ---------------------------------------------------------------------------


async def _tool_cut(
    source: Path,
    run_id: str,
    states: dict[int, _ClipState],
    args: dict[str, Any],
) -> str:
    try:
        clip_index = int(args["clip_index"])
        start = float(args["start_ts"])
        end = float(args["end_ts"])
    except (KeyError, TypeError, ValueError):
        return "ERROR: cut requires clip_index, start_ts, end_ts"
    if clip_index not in states:
        return f"ERROR: clip_index {clip_index} not in {{1,2,3}}"
    if end <= start:
        return f"ERROR: end_ts ({end}) must exceed start_ts ({start})"

    state = states[clip_index]
    state.ranges = [_TimeRange(start_ts=start, end_ts=end)]
    cut_path = paths.work_dir(run_id) / f"clip_{clip_index}_cut.mp4"
    await cut_reencode(source, start, end, cut_path)
    state.cut_path = cut_path
    size_mb = cut_path.stat().st_size / (1024 * 1024)
    return f"cut clip_{clip_index}: {end - start:.1f}s, {size_mb:.2f} MB"


async def _tool_splice(
    source: Path,
    run_id: str,
    states: dict[int, _ClipState],
    args: dict[str, Any],
) -> str:
    try:
        clip_index = int(args["clip_index"])
        raw_ranges = args["ranges"]
        ranges = [
            _TimeRange(start_ts=float(r["start_ts"]), end_ts=float(r["end_ts"]))
            for r in raw_ranges
        ]
    except (KeyError, TypeError, ValueError):
        return "ERROR: splice requires clip_index and ranges=[{start_ts,end_ts},...]"
    if clip_index not in states:
        return f"ERROR: clip_index {clip_index} not in {{1,2,3}}"
    if len(ranges) < 2:
        return "ERROR: splice needs at least 2 ranges — use `cut` for a single range"
    for r in ranges:
        if r.end_ts <= r.start_ts:
            return f"ERROR: range [{r.start_ts}-{r.end_ts}] has end_ts <= start_ts"

    state = states[clip_index]
    # Order ranges chronologically so the spliced output reads natural-forward.
    ranges_sorted = sorted(ranges, key=lambda r: r.start_ts)
    state.ranges = ranges_sorted

    slice_paths: list[Path] = []
    for i, r in enumerate(ranges_sorted):
        slice_path = paths.work_dir(run_id) / f"clip_{clip_index}_slice_{i}.mp4"
        await cut_reencode(source, r.start_ts, r.end_ts, slice_path)
        slice_paths.append(slice_path)

    cut_path = paths.work_dir(run_id) / f"clip_{clip_index}_cut.mp4"
    await concat_reencode(slice_paths, cut_path)
    state.cut_path = cut_path
    size_mb = cut_path.stat().st_size / (1024 * 1024)
    total_dur = sum(r.end_ts - r.start_ts for r in ranges_sorted)
    return (
        f"spliced clip_{clip_index}: {len(ranges_sorted)} ranges, "
        f"{total_dur:.1f}s total, {size_mb:.2f} MB"
    )


async def _tool_crop(
    run_id: str,
    states: dict[int, _ClipState],
    args: dict[str, Any],
) -> str:
    try:
        clip_index = int(args["clip_index"])
    except (KeyError, TypeError, ValueError):
        return "ERROR: crop_9_16 requires clip_index"
    if clip_index not in states:
        return f"ERROR: clip_index {clip_index} not in {{1,2,3}}"
    state = states[clip_index]
    if state.cut_path is None or not state.cut_path.exists():
        return "ERROR: call cut before crop_9_16"

    cropped_path = paths.work_dir(run_id) / f"clip_{clip_index}_cropped.mp4"
    await crop_9_16_center(state.cut_path, cropped_path)
    state.cropped_path = cropped_path
    size_mb = cropped_path.stat().st_size / (1024 * 1024)
    return f"cropped clip_{clip_index} to 1080x1920: {size_mb:.2f} MB"


async def _tool_burn_captions(
    transcript: Transcript,
    run_id: str,
    states: dict[int, _ClipState],
    args: dict[str, Any],
    *,
    burn: bool,
) -> str:
    """Always persists caption text in the manifest. Pixel-burning is opt-in
    via `burn=True` (CLI `--burn-captions`, UI checkbox)."""
    try:
        clip_index = int(args["clip_index"])
    except (KeyError, TypeError, ValueError):
        return "ERROR: burn_captions requires clip_index"
    if clip_index not in states:
        return f"ERROR: clip_index {clip_index} not in {{1,2,3}}"
    state = states[clip_index]
    if state.cropped_path is None or not state.cropped_path.exists():
        return "ERROR: call crop_9_16 before burn_captions"

    # Always emit the SRT alongside the clip — handy for manual editing and for
    # re-runs with `--burn-captions` without needing the full pipeline again.
    srt_path = paths.work_dir(run_id) / f"clip_{clip_index}.srt"
    srt_text, caption_body = _transcript_to_srt(transcript, state.ranges)
    srt_path.write_text(srt_text)
    state.caption_text = caption_body

    final_path = paths.clip_path(run_id, clip_index)
    segments = _transcript_to_segments(transcript, state.ranges)

    if not burn or not segments:
        # Captioning disabled, or no speech in this window. Cropped mp4
        # becomes the final clip; caption text (if any) stays in the manifest.
        if final_path.exists():
            final_path.unlink()
        state.cropped_path.rename(final_path)
        state.final_path = final_path
        reason = "burn disabled" if not burn else "no speech in window"
        return f"clip_{clip_index} finalized without burning captions ({reason})"

    try:
        await ffmpeg_burn_captions(state.cropped_path, segments, final_path)
        state.final_path = final_path
        size_mb = final_path.stat().st_size / (1024 * 1024)
        return f"burned captions onto clip_{clip_index}: {size_mb:.2f} MB"
    except (RuntimeError, ValueError) as exc:
        # Overlay filter should always be available, but keep a fail-soft path
        # so a bad font / missing asset can't kill the pipeline.
        log.warning(
            "editor.burn_captions_failed",
            clip_index=clip_index,
            error=str(exc)[:300],
        )
        if final_path.exists():
            final_path.unlink()
        state.cropped_path.rename(final_path)
        state.final_path = final_path
        return (
            f"clip_{clip_index} finalized without burned captions "
            f"(caption burn failed; caption text kept in manifest)"
        )


# ---------------------------------------------------------------------------
# Helpers — SRT emission, prompt rendering, backstop finishing.
# ---------------------------------------------------------------------------


def _transcript_to_segments(
    transcript: Transcript,
    ranges: list[_TimeRange] | list[tuple[float, float]],
) -> list[tuple[float, float, str]]:
    """Flatten transcript segments inside the given ranges onto the spliced
    timeline. Each tuple is `(start_s, end_s, text)` with timestamps already
    remapped so range-i contributions land at cumulative offset
    `sum(duration_0..i-1)` — matching the concatenated output mp4.
    """
    norm: list[_TimeRange] = [
        r if isinstance(r, _TimeRange) else _TimeRange(start_ts=r[0], end_ts=r[1])
        for r in ranges
    ]
    out: list[tuple[float, float, str]] = []
    cumulative_offset = 0.0
    for r in norm:
        segs = [s for s in transcript.segments if s.end > r.start_ts and s.start < r.end_ts]
        range_duration = r.end_ts - r.start_ts
        for seg in segs:
            local_start = max(0.0, seg.start - r.start_ts)
            local_end = min(range_duration, seg.end - r.start_ts)
            if local_end <= local_start:
                continue
            text = seg.text.strip()
            if not text:
                continue
            out.append((
                cumulative_offset + local_start,
                cumulative_offset + local_end,
                text,
            ))
        cumulative_offset += range_duration
    return out


def _transcript_to_srt(
    transcript: Transcript,
    ranges: list[_TimeRange] | list[tuple[float, float]],
) -> tuple[str, str]:
    """Render transcript segments inside the given ranges as an SRT on the
    spliced timeline. Thin wrapper over `_transcript_to_segments`.

    Returns (srt_text, concatenated_caption_text).
    """
    segments = _transcript_to_segments(transcript, ranges)
    if not segments:
        return "", ""
    lines: list[str] = []
    body_parts: list[str] = []
    for i, (start, end, text) in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{_fmt_srt_ts(start)} --> {_fmt_srt_ts(end)}")
        lines.append(text)
        lines.append("")
        body_parts.append(text)
    return "\n".join(lines), " ".join(body_parts)


def _fmt_srt_ts(seconds: float) -> str:
    """Format seconds as SRT timestamp `HH:MM:SS,mmm`."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _build_editor_user_prompt(
    top3: list[Candidate],
    transcript: Transcript,
    run_id: str,
) -> str:
    """Build the user-prompt body: candidates + (truncated) transcript.

    The transcript is included so the Editor can decide whether a clip's
    boundaries split a sentence and whether the theme is reinforced
    elsewhere in the video (→ motivates `strategy="splice"`). Truncated
    at TRANSCRIPT_CHAR_BUDGET to keep the context window bounded."""
    full_text_lines = []
    for seg in transcript.segments:
        text = seg.text.strip()
        if text:
            full_text_lines.append(f"[{seg.start:.1f}-{seg.end:.1f}] {text}")
    transcript_block = "\n".join(full_text_lines)
    if len(transcript_block) > TRANSCRIPT_CHAR_BUDGET:
        transcript_block = transcript_block[:TRANSCRIPT_CHAR_BUDGET] + "\n[...truncated]"

    lines = [
        f"Run id: {run_id}. Source video duration: {transcript.duration:.1f}s.",
        "",
        "Scout proposed these top-3 candidates (already ranked by composite score):",
    ]
    for i, c in enumerate(top3, start=1):
        lines.append(
            f"  clip_{i}: [{c.start_ts:.1f}-{c.end_ts:.1f}]s  "
            f"composite={c.scores.composite:.2f}  hook={c.hook!r}  "
            f"rationale={c.rationale[:180]!r}"
        )
    lines.extend([
        "",
        "Transcript (segment timestamps in seconds):",
        transcript_block,
        "",
        "Return an EditPlan with exactly 3 ClipEdits (clip_index 1, 2, 3).",
        "For each clip, choose strategy:",
        "  - strategy='cut'   → one range (30–90 s). Use when the candidate's",
        "                       moment is self-contained.",
        "  - strategy='splice'→ 2–5 ranges drawn from different parts of the",
        "                       transcript. Use when the hook is reinforced",
        "                       by related moments elsewhere — you're building",
        "                       a themed compilation. Total duration can exceed",
        "                       90 s.",
        "Adjust range boundaries by up to ±2 s off Scout's proposal to land on",
        "sentence boundaries in the transcript. For splice, draw extra ranges",
        "from the transcript above that reinforce the clip's hook/rationale.",
        "Do not invent content; every range must correspond to real transcript",
        "timestamps from this source.",
    ])
    return "\n".join(lines)


async def _finish_skipped(
    state: _ClipState,
    source: Path,
    transcript: Transcript,
    run_id: str,
    *,
    burn_captions: bool,
) -> None:
    """Backstop: if the Editor skipped any step for a clip, run the missing
    ones deterministically. Never invents data — only completes the pipeline
    stages the Editor didn't execute."""
    log.info(
        "editor.backstop_finish",
        clip_index=state.clip_index,
        missing_cut=state.cut_path is None,
        missing_cropped=state.cropped_path is None,
    )
    if state.cut_path is None or not state.cut_path.exists():
        # Fall back to the candidate's original boundaries as a single-range cut.
        state.ranges = [
            _TimeRange(start_ts=state.candidate.start_ts, end_ts=state.candidate.end_ts)
        ]
        state.cut_path = paths.work_dir(run_id) / f"clip_{state.clip_index}_cut.mp4"
        await cut_reencode(
            source, state.candidate.start_ts, state.candidate.end_ts, state.cut_path
        )
    if state.cropped_path is None or not state.cropped_path.exists():
        state.cropped_path = paths.work_dir(run_id) / f"clip_{state.clip_index}_cropped.mp4"
        await crop_9_16_center(state.cut_path, state.cropped_path)
    if state.final_path is None or not state.final_path.exists():
        srt_path = paths.work_dir(run_id) / f"clip_{state.clip_index}.srt"
        srt_text, caption_body = _transcript_to_srt(transcript, state.ranges)
        final_path = paths.clip_path(run_id, state.clip_index)
        segments = _transcript_to_segments(transcript, state.ranges)
        if srt_text.strip():
            srt_path.write_text(srt_text)
            state.caption_text = caption_body
        if not burn_captions or not segments:
            if final_path.exists():
                final_path.unlink()
            state.cropped_path.rename(final_path)
        else:
            try:
                await ffmpeg_burn_captions(state.cropped_path, segments, final_path)
            except (RuntimeError, ValueError) as exc:
                log.warning(
                    "editor.backstop_burn_failed",
                    clip_index=state.clip_index,
                    error=str(exc)[:300],
                )
                if final_path.exists():
                    final_path.unlink()
                state.cropped_path.rename(final_path)
        state.final_path = final_path


def _persist_trace(run_id: str, trace: list[dict[str, Any]]) -> None:
    """Write the editor's tool-call history as JSON-lines for the review UI."""
    if not trace:
        return
    path = paths.reasoning_trace_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for entry in trace:
            f.write(json.dumps(entry) + "\n")
    log.info("editor.trace_persisted", path=str(path), entries=len(trace))
