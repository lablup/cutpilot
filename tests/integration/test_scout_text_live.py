"""Live integration test for `scout_text_core` against the real text NIM."""

from __future__ import annotations

import socket
from pathlib import Path
from urllib.parse import urlparse

import pytest

from cutpilot import paths, prompts
from cutpilot.agents.scout import scout_text_core
from cutpilot.models import Transcript, TranscriptSegment
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


def _hand_built_transcript(duration: float = 600.0) -> Transcript:
    """Synthetic transcript with 4 clearly-distinct topic segments. Gives the
    text NIM enough structure to return varied candidates."""
    segs = [
        TranscriptSegment(
            text=(
                "Welcome everyone. Today I'll walk through how AI is changing "
                "clinical drug discovery. The first topic is protein design."
            ),
            start=0.0, end=120.0, words=[],
        ),
        TranscriptSegment(
            text=(
                "Next, a demo of real-time genomic diagnosis for newborn "
                "screening. Watch the screen as the pipeline classifies variants."
            ),
            start=120.0, end=300.0, words=[],
        ),
        TranscriptSegment(
            text=(
                "Now the big reveal: our new surgical-robotics simulator. "
                "Robots can practice procedures in a digital twin before touching a patient."
            ),
            start=300.0, end=480.0, words=[],
        ),
        TranscriptSegment(
            text=(
                "Wrapping up. Our AI factory will power the next generation of "
                "healthcare companies. Thank you."
            ),
            start=480.0, end=duration, words=[],
        ),
    ]
    return Transcript(
        source_path=Path("/tmp/synthetic.mp4"),
        language="en",
        duration=duration,
        segments=segs,
    )


async def test_scout_text_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _reachable(settings.nim_text_base_url):
        pytest.skip(f"Text NIM not reachable at {settings.nim_text_base_url}")

    monkeypatch.setattr(settings, "cutpilot_work_dir", tmp_path / "work")
    paths.ensure_dirs("text-live")

    result = await scout_text_core(
        transcript=_hand_built_transcript(),
        run_id="text-live",
        system_prompt=prompts.load("scout"),
    )
    assert 5 <= len(result.candidates) <= 10
    hooks = [c.hook.lower() for c in result.candidates]
    # 4 well-separated topics should yield at least 3 different hooks.
    assert len({h[:20] for h in hooks}) >= 3, f"hooks too similar: {hooks}"
    for c in result.candidates:
        assert c.end_ts > c.start_ts
        duration = c.end_ts - c.start_ts
        assert 20.0 <= duration <= 90.0
