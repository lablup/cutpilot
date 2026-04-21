"""Shared test fixtures. Generates tiny media files via `ffmpeg lavfi` so
integration tests have real inputs without any binary assets in the repo."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


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


@pytest.fixture(scope="session")
def scout_test_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """180-second 640x360 mp4 — long enough for Scout's min-5 × 20-s contract.

    `testsrc` embeds a moving clock and a frame counter, so VL models have enough
    structural variation to propose distinct candidates. Session-scoped so the
    expensive ffmpeg generation runs once per test session.
    """
    if not _ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    out = tmp_path_factory.mktemp("fixtures") / "scout_test.mp4"
    _lavfi([
        "-f", "lavfi", "-i", "testsrc=duration=180:size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=180",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "64k",
        "-shortest",
        str(out),
    ])
    return out
