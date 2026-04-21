"""Live integration test for `scout_text_core` against the real text NIM,
fed a real Whisper transcript of a 120-second slice of the GTC talk.

No synthetic/hand-built transcript — per project policy, integration tests
use live dependencies + real content end-to-end.
"""

from __future__ import annotations

import socket
from pathlib import Path
from urllib.parse import urlparse

import pytest

from cutpilot import paths, prompts
from cutpilot.agents.scout import scout_text_core
from cutpilot.models import Transcript
from cutpilot.settings import settings

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _reachable(url: str, timeout: float = 3.0) -> bool:
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


async def test_scout_text_live(
    gtc_slice_transcript: Transcript,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _reachable(settings.nim_text_base_url):
        pytest.skip(f"Text NIM not reachable at {settings.nim_text_base_url}")

    monkeypatch.setattr(settings, "cutpilot_work_dir", tmp_path / "work")
    paths.ensure_dirs("text-live")

    result = await scout_text_core(
        transcript=gtc_slice_transcript,
        run_id="text-live",
        system_prompt=prompts.load("scout"),
    )
    assert 5 <= len(result.candidates) <= 10
    hooks = [c.hook.lower() for c in result.candidates]
    # Real-content transcript should produce meaningfully distinct hooks.
    assert len({h[:20] for h in hooks}) >= 3, f"hooks too similar: {hooks}"
    for c in result.candidates:
        assert c.end_ts > c.start_ts
        duration = c.end_ts - c.start_ts
        assert 20.0 <= duration <= 90.0
