"""`save` tool — re-encode a video to a shareable MP4 (H.264 + AAC + faststart)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig


class SaveConfig(FunctionBaseConfig, name="cutpilot_save"):
    pass


@register_function(config_type=SaveConfig, framework_wrappers=[LLMFrameworkEnum.ADK])
async def register(_config: SaveConfig, _builder: Builder) -> AsyncIterator[FunctionInfo]:
    from cutpilot.clients.ffmpeg import export_standard

    async def _save(source_path: str, output_path: str) -> str:
        """Export a video to a standard shareable MP4.

        Re-encodes with libx264 (preset=fast, CRF=20), AAC audio at 128 kbps, and
        `-movflags +faststart` so the file plays from the first byte when streamed.

        Args:
            source_path: Absolute path to the input video.
            output_path: Absolute path where the exported MP4 should be written.

        Returns:
            The absolute path of the written file.
        """
        await export_standard(Path(source_path), Path(output_path))
        return output_path

    yield FunctionInfo.from_fn(_save, description=_save.__doc__)
