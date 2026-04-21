"""Factories for NIM-backed LLM handles.

These mirror what NAT's `nvidia_nat_adk` plugin yields when the YAML says
`_type: nim`, but are usable from plain Python (no builder, no config file).
Sharing one factory keeps `pipeline.py` and `scripts/scout_smoke.py` honest
about the `media_io_kwargs` knob — forgetting it once produced identical-text
candidates in the GTC run.
"""

from __future__ import annotations

import os

from google.adk.models.lite_llm import LiteLlm

from cutpilot.settings import settings


def make_vl_llm() -> LiteLlm:
    """LiteLlm pointed at the Nemotron Nano VL NIM, with per-request frame sampling.

    `extra_body.media_io_kwargs.num_frames=128` is load-bearing: without it the
    NIM defaults to ~8 frames and Scout can't tell one moment from another on
    videos longer than ~2 minutes.
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
