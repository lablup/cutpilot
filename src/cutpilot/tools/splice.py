"""`splice` tool — concatenate clips end-to-end via the ffmpeg concat demuxer.

Auto-retries the `-c copy` fast path with a re-encode fallback, matching the
`cut` tool's copy/reencode retry pattern.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig


class SpliceConfig(FunctionBaseConfig, name="cutpilot_splice"):
    pass


@register_function(config_type=SpliceConfig, framework_wrappers=[LLMFrameworkEnum.ADK])
async def register(_config: SpliceConfig, _builder: Builder) -> AsyncIterator[FunctionInfo]:
    from cutpilot.clients.ffmpeg import concat_copy, concat_reencode

    async def _splice(source_paths: list[str], output_path: str) -> str:
        """Splice two or more video files end-to-end into a single output video.

        Tries the fast `-c copy` path first; falls back to re-encode if ffmpeg
        reports an error (typical when inputs have mismatched codecs, framerates,
        or resolutions).

        Args:
            source_paths: Absolute paths of the input clips, in playback order.
                At least two paths are required for a meaningful splice.
            output_path: Absolute path where the spliced video should be written.

        Returns:
            The absolute path of the written file.
        """
        sources = [Path(p) for p in source_paths]
        out = Path(output_path)
        try:
            await concat_copy(sources, out)
        except RuntimeError:
            await concat_reencode(sources, out)
        return str(out)

    yield FunctionInfo.from_fn(_splice, description=_splice.__doc__)
