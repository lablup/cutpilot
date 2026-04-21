"""`transcript_window` tool — read-only slice of the transcript, for boundary refinement."""

from __future__ import annotations

from collections.abc import AsyncIterator

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig

from cutpilot import paths, persistence
from cutpilot.models import Transcript


class TranscriptWindowConfig(FunctionBaseConfig, name="cutpilot_transcript_window"):
    pass


@register_function(config_type=TranscriptWindowConfig, framework_wrappers=[LLMFrameworkEnum.ADK])
async def register(
    _config: TranscriptWindowConfig,
    _builder: Builder,
) -> AsyncIterator[FunctionInfo]:
    async def _transcript_window(run_id: str, start_ts: float, end_ts: float) -> str:
        """Return the transcript text that falls within [start_ts, end_ts] seconds.

        Args:
            run_id: The pipeline run identifier.
            start_ts: Window start in seconds.
            end_ts: Window end in seconds. Must be greater than start_ts.

        Returns:
            Concatenated segment text within the window. Empty string if nothing falls in it.
        """
        transcript = persistence.load(Transcript, paths.transcript_json_path(run_id))
        hits = [seg.text for seg in transcript.segments if seg.end > start_ts and seg.start < end_ts]
        return " ".join(hits)

    yield FunctionInfo.from_fn(_transcript_window, description=_transcript_window.__doc__)
