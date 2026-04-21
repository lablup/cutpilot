"""`crop_9_16` tool — center-crop horizontal source to 1080×1920 vertical."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig


class CropConfig(FunctionBaseConfig, name="cutpilot_crop_9_16"):
    pass


@register_function(config_type=CropConfig, framework_wrappers=[LLMFrameworkEnum.ADK])
async def register(_config: CropConfig, _builder: Builder) -> AsyncIterator[FunctionInfo]:
    from cutpilot.clients.ffmpeg import crop_9_16_center

    async def _crop_9_16(source_path: str, output_path: str) -> str:
        """Center-crop a horizontal video to a 1080×1920 vertical video.

        Args:
            source_path: Absolute path to the input video.
            output_path: Absolute path where the cropped output should be written.

        Returns:
            The absolute path of the written file.
        """
        await crop_9_16_center(Path(source_path), Path(output_path))
        return output_path

    yield FunctionInfo.from_fn(_crop_9_16, description=_crop_9_16.__doc__)
