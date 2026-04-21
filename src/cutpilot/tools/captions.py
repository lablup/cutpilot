"""`burn_captions` tool — burn full-segment subtitles into a video via ffmpeg."""

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


@register_function(config_type=BurnCaptionsConfig, framework_wrappers=[LLMFrameworkEnum.ADK])
async def register(
    _config: BurnCaptionsConfig,
    _builder: Builder,
) -> AsyncIterator[FunctionInfo]:
    from cutpilot.clients.ffmpeg import burn_captions as _burn

    async def _burn_captions(source_path: str, srt_path: str, output_path: str) -> str:
        """Burn subtitles from an SRT file into the video as hard-coded captions.

        Args:
            source_path: Absolute path to the input video.
            srt_path: Absolute path to a valid .srt subtitle file.
            output_path: Absolute path where the captioned video should be written.

        Returns:
            The absolute path of the written file.
        """
        await _burn(Path(source_path), Path(srt_path), Path(output_path))
        return output_path

    yield FunctionInfo.from_fn(_burn_captions, description=_burn_captions.__doc__)
