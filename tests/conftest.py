"""Shared test fixtures.

`tiny_*` fixtures generate content-free ffmpeg lavfi clips — only use them
from tests that exercise the ffmpeg subprocess itself (cut/crop/burn).
Any test that sends pixels or audio to an LLM/VL/ASR NIM must use
`gtc_slice_*` instead — synthetic content makes LLM output meaningless."""

from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import pytest

from cutpilot.clients.ffmpeg import extract_audio
from cutpilot.clients.whisper import transcribe
from cutpilot.models import Transcript
from cutpilot.settings import settings

GTC_VIDEO = (
    Path(__file__).resolve().parents[1]
    / "sources"
    / "NVIDIA GTC DC 2025： Healthcare Special Address [cW_POtTfJVM].mp4"
)


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


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


def _lavfi(args: list[str]) -> None:
    """Run `ffmpeg` with the given args, raising a readable error on failure."""
    result = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg fixture generation failed: {result.stderr.decode(errors='replace')}"
        )


@pytest.fixture(scope="session")
def tiny_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """3-second 320x240 H.264 + AAC MP4 at 30 fps. Skips if ffmpeg is absent."""
    if not _ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    out = tmp_path_factory.mktemp("fixtures") / "tiny.mp4"
    _lavfi([
        "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "64k",
        "-shortest",
        str(out),
    ])
    return out


@pytest.fixture(scope="session")
def tiny_video_noaudio(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """3-second 320x240 H.264-only MP4 at 30 fps. Skips if ffmpeg is absent."""
    if not _ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    out = tmp_path_factory.mktemp("fixtures") / "tiny_noaudio.mp4"
    _lavfi([
        "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=30",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-an",
        str(out),
    ])
    return out


@pytest.fixture(scope="session")
def tiny_audio(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """3-second AAC M4A audio track. Skips if ffmpeg is absent."""
    if not _ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    out = tmp_path_factory.mktemp("fixtures") / "tiny.m4a"
    _lavfi([
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:a", "aac", "-b:a", "64k",
        str(out),
    ])
    return out


# ---------------------------------------------------------------------------
# Real-content fixtures — 120-second slice of the downloaded GTC talk.
# Required for any integration test that feeds pixels/audio to an LLM NIM.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def gtc_slice_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """120-second slice of the real GTC Healthcare talk (starts 60s in to
    skip the title card). Session-scoped — one ffmpeg cut per test run.
    Skips if the GTC video or ffmpeg is missing."""
    if not _ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    if not GTC_VIDEO.exists():
        pytest.skip(f"GTC source video missing at {GTC_VIDEO}")
    out = tmp_path_factory.mktemp("fixtures") / "gtc_slice.mp4"
    # Re-encode (not `-c copy`) so we land on a keyframe at ss=60 — cleaner
    # input for VL and avoids the first-frame-black artifact on copy cuts.
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", "60", "-i", str(GTC_VIDEO), "-t", "120",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k",
            str(out),
        ],
        check=True,
    )
    return out


@pytest.fixture(scope="session")
def gtc_slice_transcript(
    gtc_slice_video: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> Transcript:
    """Real Whisper transcript of the 120s GTC slice. Session-scoped so only
    one Whisper round-trip happens per run. Skips if the Whisper NIM is
    unreachable."""
    if not _reachable(settings.whisper_base_url):
        pytest.skip(f"Whisper NIM not reachable at {settings.whisper_base_url}")
    work = tmp_path_factory.mktemp("fixtures_audio")
    audio = work / "gtc_slice.wav"
    chunks_dir = work / "chunks"

    async def _run() -> Transcript:
        await extract_audio(gtc_slice_video, audio)
        return await transcribe(
            audio_path=audio,
            source_path=gtc_slice_video,
            chunks_dir=chunks_dir,
        )

    return asyncio.run(_run())
