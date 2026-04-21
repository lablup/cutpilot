"""Scout smoke test — runs Scout end-to-end against the VL NIM, no NAT workflow.

Usage:
    python scripts/scout_smoke.py <video_path> [run_id]

Reads `.env` via `cutpilot.settings` for `NIM_VL_BASE_URL`, `NIM_VL_MODEL`, and
`NVIDIA_API_KEY`. Bypasses `pipeline.py` and the NAT `sequential_executor` so it
can be exercised without Whisper or the Editor.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import structlog
from google.adk.models.lite_llm import LiteLlm

from cutpilot import paths, prompts
from cutpilot.agents.scout import scout_core
from cutpilot.settings import settings

log = structlog.get_logger()


def _make_llm() -> LiteLlm:
    """Build a bare LiteLlm handle for the VL NIM, matching what NAT's adk plugin yields.

    `extra_body.media_io_kwargs` pushes NIM-side frame sampling to the Nemotron Nano
    VL's max (128). Without it, NIM defaults to ~8 frames and the model returns
    near-duplicate descriptions for every candidate because it literally can't see
    past the title slide.
    """
    import litellm
    litellm.drop_params = True
    if settings.nvidia_api_key:
        os.environ["NVIDIA_NIM_API_KEY"] = settings.nvidia_api_key
    return LiteLlm(
        f"nvidia_nim/{settings.nim_vl_model}",
        api_base=settings.nim_vl_base_url,
        extra_body={"media_io_kwargs": {"video": {"fps": 2, "num_frames": 128}}},
    )


async def _main(video: Path, run_id: str) -> None:
    paths.ensure_dirs(run_id)
    llm = _make_llm()
    log.info(
        "smoke.start",
        video=str(video),
        run_id=run_id,
        nim_vl_base_url=settings.nim_vl_base_url,
        nim_vl_model=settings.nim_vl_model,
    )
    result = await scout_core(
        llm=llm,
        source_path=video,
        run_id=run_id,
        system_prompt=prompts.load("scout"),
        transcript=None,
    )
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python scripts/scout_smoke.py <video_path> [run_id]", file=sys.stderr)
        raise SystemExit(2)
    video_path = Path(sys.argv[1])
    if not video_path.exists():
        print(f"video not found: {video_path}", file=sys.stderr)
        raise SystemExit(2)
    run_id = sys.argv[2] if len(sys.argv) > 2 else "smoke"
    asyncio.run(_main(video_path, run_id))
