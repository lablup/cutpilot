"""Live integration test for `scout_vl_sliding` on a 120-second slice of the
real GTC talk. Exercises the parallel-gather + per-window-persistence path
against genuine content so VL scores/hooks are meaningful."""

from __future__ import annotations

import socket
from pathlib import Path
from urllib.parse import urlparse

import pytest

from cutpilot import paths
from cutpilot.agents.scout import scout_vl_sliding
from cutpilot.clients.ffmpeg import probe_duration, prepare_video_for_vl
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
    gtc_slice_video: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not _reachable(settings.nim_vl_base_url):
        pytest.skip(f"VL NIM not reachable at {settings.nim_vl_base_url}")

    monkeypatch.setattr(settings, "cutpilot_work_dir", tmp_path / "work")
    run_id = "sliding-live"
    paths.ensure_dirs(run_id)

    # The sliding scanner wants a VL-ready (480p, low-CRF, audio-dropped) mp4.
    vl_video = paths.vl_video_path(run_id)
    await prepare_video_for_vl(gtc_slice_video, vl_video)
    duration = await probe_duration(vl_video)

    # 3 windows × ~30s each — enough VL surface area for real hooks, fast
    # enough not to dominate the integration suite's wall-clock.
    windows = await scout_vl_sliding(
        vl_video_path=vl_video,
        duration=duration,
        run_id=run_id,
        n_windows=3,
        window_len_s=30.0,
        concurrency=3,
    )

    # Allow one flaky NIM response; require at least a majority.
    assert len(windows) >= 2, f"too few windows scored: {len(windows)}"
    for w in windows:
        assert 1 <= w.visual_score <= 5
        assert w.visual_hook.strip() != ""
        assert w.end_ts > w.start_ts

    # Real speaker-on-stage content should produce distinct per-window hooks,
    # not collapsed duplicates like the old testsrc fixture.
    hooks = [w.visual_hook.strip().lower() for w in windows]
    assert len({h[:40] for h in hooks}) >= 2, f"hooks collapsed: {hooks}"

    persisted = sorted((tmp_path / "work" / run_id / "vl_windows").glob("window_*.mp4"))
    assert len(persisted) >= len(windows)
