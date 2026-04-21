"""Scout — deterministic function that calls the NIM VL endpoint once and returns
a validated `CandidatesResult`. No tools, no agent loop. The function's return type
is the schema."""

from __future__ import annotations

from collections.abc import AsyncIterator

import structlog
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import LLMRef
from nat.data_models.function import FunctionBaseConfig

from cutpilot import paths, persistence, prompts
from cutpilot.models import CandidatesResult, Transcript

log = structlog.get_logger()


class ScoutConfig(FunctionBaseConfig, name="cutpilot_scout"):
    """Scout is bound to the VL LLM (Nemotron Nano 12B V2 VL on NIM)."""

    llm: LLMRef


@register_function(config_type=ScoutConfig, framework_wrappers=[LLMFrameworkEnum.ADK])
async def register(config: ScoutConfig, builder: Builder) -> AsyncIterator[FunctionInfo]:
    system_prompt = prompts.load("scout")
    llm = await builder.get_llm(config.llm, wrapper_type=LLMFrameworkEnum.LANGCHAIN)

    async def _scout(run_id: str, source_path: str) -> CandidatesResult:
        """Propose 5–10 candidate short-form clip moments from a long-form video.

        Uses the VL NIM endpoint to read the video + transcript in a single pass and
        returns a validated `CandidatesResult`. Each candidate carries start/end
        timestamps, a hook description, a rationale, and a 1–5 self-score on hook,
        self-contained, length-fit, and visual-fit.

        Args:
            run_id: The pipeline run identifier.
            source_path: Absolute path to the source video.

        Returns:
            A validated `CandidatesResult` with between 5 and 10 candidates.
        """
        transcript = persistence.load(Transcript, paths.transcript_json_path(run_id))
        log.info(
            "scout.start",
            run_id=run_id,
            source=source_path,
            transcript_segments=len(transcript.segments),
            system_prompt_chars=len(system_prompt),
        )
        # TODO: construct the multimodal message (video URL + transcript text),
        # call `llm.with_structured_output(CandidatesResult).ainvoke(...)`, return the
        # validated result. Pydantic will raise ValidationError on malformed output —
        # that's the fail-closed contract.
        raise NotImplementedError(
            "Wire VL multimodal call + with_structured_output(CandidatesResult)."
        )

    yield FunctionInfo.from_fn(_scout, description=_scout.__doc__)
