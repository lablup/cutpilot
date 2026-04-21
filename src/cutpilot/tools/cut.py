"""`cut` tool — ffmpeg time-range extraction. Registered as a NAT component;
decorated `framework_wrappers=[LLMFrameworkEnum.ADK]` so it's portable to the
`_type: adk` workflow if we ever switch."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig


class CutConfig(FunctionBaseConfig, name="cutpilot_cut"):
    pass


@register_function(config_type=CutConfig, framework_wrappers=[LLMFrameworkEnum.ADK])
async def register(_config: CutConfig, _builder: Builder) -> AsyncIterator[FunctionInfo]:
    from cutpilot.clients.ffmpeg import cut_copy, cut_reencode

    async def _cut(source_path: str, start_ts: float, end_ts: float, output_path: str) -> str:
        """Extract a time range from a video file. Tries `-c copy` first for speed;
        falls back to re-encode if ffmpeg reports an error.

        Args:
            source_path: Absolute path to the input video.
            start_ts: Start time in seconds.
            end_ts: End time in seconds. Must be greater than start_ts.
            output_path: Absolute path where the cut clip should be written.

        Returns:
            The absolute path of the written file.
        """
        src = Path(source_path)
        out = Path(output_path)
        try:
            await cut_copy(src, start_ts, end_ts, out)
        except RuntimeError:
            await cut_reencode(src, start_ts, end_ts, out)
        return str(out)

    yield FunctionInfo.from_fn(_cut, description=_cut.__doc__)
