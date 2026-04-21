"""`merge` tool — mux a separate video and audio track into one MP4 with `-c copy`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig


class MergeConfig(FunctionBaseConfig, name="cutpilot_merge"):
    pass


@register_function(config_type=MergeConfig, framework_wrappers=[LLMFrameworkEnum.ADK])
async def register(_config: MergeConfig, _builder: Builder) -> AsyncIterator[FunctionInfo]:
    from cutpilot.clients.ffmpeg import mux_av

    async def _merge(video_path: str, audio_path: str, output_path: str) -> str:
        """Merge a video-only file and an audio-only file into a single MP4.

        Takes the first video stream from `video_path` and the first audio stream
        from `audio_path`, truncating to the shorter duration.

        Args:
            video_path: Absolute path to the video source (audio ignored if present).
            audio_path: Absolute path to the audio source (video ignored if present).
            output_path: Absolute path where the merged MP4 should be written.

        Returns:
            The absolute path of the written file.
        """
        await mux_av(Path(video_path), Path(audio_path), Path(output_path))
        return output_path

    yield FunctionInfo.from_fn(_merge, description=_merge.__doc__)
