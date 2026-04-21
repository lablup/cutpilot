"""`probe` tool — read compact media metadata via `ffprobe`.

Returns a JSON string so the tool return type matches the rest of the toolkit
(agent consumers treat tool output as strings uniformly); the underlying shape
is `cutpilot.models.ProbeInfo`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig


class ProbeConfig(FunctionBaseConfig, name="cutpilot_probe"):
    pass


@register_function(config_type=ProbeConfig, framework_wrappers=[LLMFrameworkEnum.ADK])
async def register(_config: ProbeConfig, _builder: Builder) -> AsyncIterator[FunctionInfo]:
    from cutpilot.clients.ffmpeg import probe_media

    async def _probe(source_path: str) -> str:
        """Inspect a media file and return compact metadata as JSON.

        The returned JSON has the shape of `cutpilot.models.ProbeInfo` — keys
        `duration, width, height, video_codec, audio_codec, fps, size_bytes`.
        Any field ffprobe did not report is `null`.

        Args:
            source_path: Absolute path to the media file.

        Returns:
            A JSON string of the narrowed probe info.
        """
        info = await probe_media(Path(source_path))
        return info.model_dump_json()

    yield FunctionInfo.from_fn(_probe, description=_probe.__doc__)
