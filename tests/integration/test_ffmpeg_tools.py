"""Live-ffmpeg integration tests for the extended editing primitives.

These tests shell out to real `ffmpeg` / `ffprobe` against the session-scoped
`tiny_video*` fixtures. They are marked `integration` so CI can run them on
a machine that has the binaries while unit runs stay lean.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cutpilot.clients.ffmpeg import (
    concat_copy,
    concat_reencode,
    export_standard,
    mux_av,
    probe_media,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_probe_reports_plausible_dimensions(tiny_video: Path) -> None:
    info = await probe_media(tiny_video)
    assert info.width == 320
    assert info.height == 240
    assert info.video_codec == "h264"
    assert info.audio_codec == "aac"
    assert info.fps == pytest.approx(30.0, rel=0.01)
    assert info.duration is not None and 2.8 < info.duration < 3.3
    assert info.size_bytes is not None and info.size_bytes > 0


async def test_concat_copy_joins_two_identical_clips(
    tiny_video: Path,
    tmp_path: Path,
) -> None:
    out = tmp_path / "spliced.mp4"
    await concat_copy([tiny_video, tiny_video], out)
    assert out.exists()

    info = await probe_media(out)
    assert info.duration is not None
    # Joined = ~6s (allow drift because concat copy is not frame-perfect across
    # keyframe boundaries on some encoders).
    assert 5.5 < info.duration < 6.6


async def test_concat_reencode_joins_two_clips(
    tiny_video: Path,
    tmp_path: Path,
) -> None:
    out = tmp_path / "spliced_reenc.mp4"
    await concat_reencode([tiny_video, tiny_video], out)
    assert out.exists()

    info = await probe_media(out)
    assert info.video_codec == "h264"
    assert info.audio_codec == "aac"
    assert info.duration is not None and 5.5 < info.duration < 6.6


async def test_mux_combines_separate_video_and_audio(
    tiny_video_noaudio: Path,
    tiny_audio: Path,
    tmp_path: Path,
) -> None:
    out = tmp_path / "merged.mp4"
    await mux_av(tiny_video_noaudio, tiny_audio, out)
    assert out.exists()

    info = await probe_media(out)
    assert info.video_codec == "h264"
    assert info.audio_codec == "aac"
    assert info.width == 320
    assert info.height == 240


async def test_export_standard_produces_faststart_h264_aac(
    tiny_video: Path,
    tmp_path: Path,
) -> None:
    out = tmp_path / "exported.mp4"
    await export_standard(tiny_video, out)
    assert out.exists()

    info = await probe_media(out)
    assert info.video_codec == "h264"
    assert info.audio_codec == "aac"
    assert info.width == 320
    assert info.height == 240
    assert info.duration is not None and 2.8 < info.duration < 3.3


async def test_probe_on_missing_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.mp4"
    with pytest.raises(RuntimeError, match="ffprobe failed"):
        await probe_media(missing)


async def test_concat_copy_error_propagates(tmp_path: Path) -> None:
    # Non-existent input → ffmpeg errors out through `_run`.
    missing = tmp_path / "nope.mp4"
    out = tmp_path / "out.mp4"
    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        await concat_copy([missing, missing], out)
