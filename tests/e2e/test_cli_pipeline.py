"""End-to-end CLI test — exercises `run_pipeline` against all three live NIMs
on the real GTC Healthcare talk we ship as the canonical demo input.

Marked `@pytest.mark.e2e` so the default `pytest` run skips it. Opt in with
`pytest -m e2e`. Requires:
  - ffmpeg on PATH
  - Whisper, text, and VL NIMs all reachable
  - `sources/NVIDIA GTC DC 2025： Healthcare Special Address [cW_POtTfJVM].mp4`
    (download with `yt-dlp https://www.youtube.com/watch?v=cW_POtTfJVM -o
    'sources/%(title)s [%(id)s].%(ext)s'` — same file the CLI smoke runs on)
"""

from __future__ import annotations

import socket
from pathlib import Path
from urllib.parse import urlparse

import pytest

from cutpilot import paths
from cutpilot.pipeline import run_pipeline
from cutpilot.settings import settings

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

GTC_VIDEO = (
    Path(__file__).resolve().parents[2]
    / "sources"
    / "NVIDIA GTC DC 2025： Healthcare Special Address [cW_POtTfJVM].mp4"
)


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


async def test_cli_pipeline_e2e(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full pipeline on the real GTC talk: Whisper → VL sliding → text scout →
    materialize → stitch. Asserts 3 distinct clips + highlights.mp4."""
    if not GTC_VIDEO.exists():
        pytest.skip(f"GTC video missing at {GTC_VIDEO} — run yt-dlp first")
    for label, url in [
        ("whisper", settings.whisper_base_url),
        ("text", settings.nim_text_base_url),
        ("vl", settings.nim_vl_base_url),
    ]:
        if not _reachable(url):
            pytest.skip(f"{label} NIM unreachable at {url}")

    monkeypatch.setattr(settings, "cutpilot_work_dir", tmp_path / "work")
    monkeypatch.setattr(settings, "cutpilot_outputs_dir", tmp_path / "outputs")
    run_id = "pytest-e2e"

    manifests = await run_pipeline(
        source=str(GTC_VIDEO),
        run_id=run_id,
    )

    assert len(manifests) == 3
    hooks = [m.hook for m in manifests]
    assert len({h[:30] for h in hooks}) == 3, f"hooks too similar: {hooks}"

    outputs = paths.run_outputs_dir(run_id)
    for i in range(1, 4):
        clip = outputs / f"clip_{i}.mp4"
        manifest = outputs / f"clip_{i}.manifest.json"
        assert clip.exists() and clip.stat().st_size > 100_000, f"missing or tiny {clip}"
        assert manifest.exists(), f"missing manifest {manifest}"
    highlights = outputs / "highlights.mp4"
    assert highlights.exists() and highlights.stat().st_size > 500_000
