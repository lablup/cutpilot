"""`burn_captions` tool — burn captions into a video as PNG overlays (no libass)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig


class BurnCaptionsConfig(FunctionBaseConfig, name="cutpilot_burn_captions"):
    pass


def _srt_ts_to_seconds(ts: str) -> float:
    """Parse an SRT timestamp `HH:MM:SS,mmm` into seconds."""
    hms, ms = ts.strip().split(",")
    h, m, s = hms.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _parse_srt(srt_text: str) -> list[tuple[float, float, str]]:
    """Parse an SRT file body into `(start_s, end_s, text)` tuples."""
    segments: list[tuple[float, float, str]] = []
    for block in srt_text.strip().split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 3:
            continue
        # lines[0] is the caption index; lines[1] is "HH:MM:SS,mmm --> HH:MM:SS,mmm"
        start_s, end_s = (t.strip() for t in lines[1].split("-->"))
        text = " ".join(lines[2:]).strip()
        if not text:
            continue
        segments.append((_srt_ts_to_seconds(start_s), _srt_ts_to_seconds(end_s), text))
    return segments


@register_function(config_type=BurnCaptionsConfig, framework_wrappers=[LLMFrameworkEnum.ADK])
async def register(
    _config: BurnCaptionsConfig,
    _builder: Builder,
) -> AsyncIterator[FunctionInfo]:
    from cutpilot.clients.ffmpeg import burn_captions as _burn

    async def _burn_captions(source_path: str, srt_path: str, output_path: str) -> str:
        """Burn captions from an SRT file into the video as PNG overlays.

        Uses the core `overlay` filter (libass-independent): each caption
        segment is rendered to a transparent PNG by Pillow, then composited
        via `enable='between(t,start,end)'`.

        Args:
            source_path: Absolute path to the input video.
            srt_path: Absolute path to a valid .srt subtitle file.
            output_path: Absolute path where the captioned video should be written.

        Returns:
            The absolute path of the written file.
        """
        segments = _parse_srt(Path(srt_path).read_text())
        if not segments:
            raise ValueError(f"burn_captions: no caption segments parsed from {srt_path}")
        await _burn(Path(source_path), segments, Path(output_path))
        return output_path

    yield FunctionInfo.from_fn(_burn_captions, description=_burn_captions.__doc__)
