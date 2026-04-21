"""Live integration test for Scout against the real VL NIM.

Runs `scout_core` end-to-end: transcode synthetic video → Part.from_bytes →
ADK LiteLlm → NIM round-trip → CandidatesResult. Marked `integration` so the
default `pytest -m "not integration"` skips it.

Requires:
  - ffmpeg on PATH
  - `NIM_VL_BASE_URL` reachable (the tunnel or a self-hosted Brev endpoint)
  - `NIM_VL_MODEL` set
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from urllib.parse import urlparse

import pytest
from google.adk.models.lite_llm import LiteLlm

from cutpilot import paths, prompts
from cutpilot.agents.scout import scout_core
from cutpilot.settings import settings

pytestmark = pytest.mark.integration


def _reachable(url: str, timeout: float = 3.0) -> bool:
    """Lightweight TCP probe of the host:port in `url`. Avoids importing httpx here."""
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if host is None:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _make_llm() -> LiteLlm:
    import litellm
    litellm.drop_params = True
    if settings.nvidia_api_key:
        os.environ["NVIDIA_NIM_API_KEY"] = settings.nvidia_api_key
    return LiteLlm(
        f"nvidia_nim/{settings.nim_vl_model}",
        api_base=settings.nim_vl_base_url,
        extra_body={"media_io_kwargs": {"video": {"fps": 2, "num_frames": 128}}},
    )


async def test_scout_live_end_to_end(
    scout_test_video: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Send a 180-s synthetic video to the real VL NIM and expect a
    validated `CandidatesResult`.

    Skips (rather than fails) when the NIM endpoint isn't reachable — keeps
    `pytest -m integration` informative when running from a laptop without
    the Cloudflare tunnel live.
    """
    if not _reachable(settings.nim_vl_base_url):
        pytest.skip(f"VL NIM not reachable at {settings.nim_vl_base_url}")

    # Isolate work dir: scout_core caches the downsampled video under work/<run_id>/
    monkeypatch.setattr(settings, "cutpilot_work_dir", tmp_path / "work")
    paths.ensure_dirs("live-smoke")

    llm = _make_llm()
    result = await scout_core(
        llm=llm,
        source_path=scout_test_video,
        run_id="live-smoke",
        system_prompt=prompts.load("scout"),
        transcript=None,
    )
    assert 5 <= len(result.candidates) <= 10
    for c in result.candidates:
        assert c.end_ts > c.start_ts
        duration = c.end_ts - c.start_ts
        assert 20.0 <= duration <= 90.0
        assert c.scores.hook in range(1, 6)
