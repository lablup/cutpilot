"""Live integration test for `scout_vl_sliding` — VL sliding scan on a small
fixture. Exercises the parallel-gather + per-window-persistence path."""

from __future__ import annotations

import socket
from pathlib import Path
from urllib.parse import urlparse

import pytest

from cutpilot import paths
from cutpilot.agents.scout import scout_vl_sliding
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


async def test_scout_sliding_live(
    tiny_video_noaudio: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _reachable(settings.nim_vl_base_url):
        pytest.skip(f"VL NIM not reachable at {settings.nim_vl_base_url}")

    monkeypatch.setattr(settings, "cutpilot_work_dir", tmp_path / "work")
    paths.ensure_dirs("sliding-live")

    # tiny_video_noaudio is 3 s; 3 windows of 1 s each exercises the sliding
    # logic without bloating wall-clock on the NIM.
    windows = await scout_vl_sliding(
        vl_video_path=tiny_video_noaudio,
        duration=3.0,
        run_id="sliding-live",
        n_windows=3,
        window_len_s=1.0,
        concurrency=3,
    )

    # Some windows may fail on flaky NIM; require majority to succeed.
    assert len(windows) >= 2, f"too few windows scored: {len(windows)}"
    for w in windows:
        assert 1 <= w.visual_score <= 5
        assert w.visual_hook.strip() != ""
        assert w.end_ts > w.start_ts

    # Per-window mp4s persisted under work/<run_id>/vl_windows/.
    persisted = sorted((tmp_path / "work" / "sliding-live" / "vl_windows").glob("window_*.mp4"))
    assert len(persisted) >= len(windows)
