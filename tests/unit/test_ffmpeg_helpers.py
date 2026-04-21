"""Unit tests for the pure helpers in `cutpilot.clients.ffmpeg`.

These run without ffmpeg on PATH — they only exercise logic that transforms
data in memory (listfile rendering, probe-dict narrowing).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cutpilot.clients.ffmpeg import _format_concat_listfile, _narrow_probe
from cutpilot.models import ProbeInfo


class TestFormatConcatListfile:
    def test_single_source(self, tmp_path: Path) -> None:
        src = tmp_path / "a.mp4"
        src.touch()
        result = _format_concat_listfile([src])
        assert result == f"file '{src.resolve()}'\n"

    def test_multiple_sources_in_order(self, tmp_path: Path) -> None:
        a = tmp_path / "a.mp4"
        b = tmp_path / "b.mp4"
        a.touch()
        b.touch()
        result = _format_concat_listfile([a, b])
        lines = result.splitlines()
        assert lines == [f"file '{a.resolve()}'", f"file '{b.resolve()}'"]

    def test_trailing_newline_present(self, tmp_path: Path) -> None:
        src = tmp_path / "a.mp4"
        src.touch()
        assert _format_concat_listfile([src]).endswith("\n")

    def test_single_quote_in_path_escaped(self, tmp_path: Path) -> None:
        # ffmpeg concat demuxer needs single quotes escaped as '\''
        quirky = tmp_path / "it's a file.mp4"
        quirky.touch()
        result = _format_concat_listfile([quirky])
        assert r"it'\''s a file.mp4" in result


class TestNarrowProbe:
    def _raw(self, **overrides: Any) -> dict[str, Any]:
        """Representative ffprobe output for an H.264 + AAC MP4."""
        default: dict[str, Any] = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "r_frame_rate": "30000/1001",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                },
            ],
            "format": {
                "duration": "12.345",
                "size": "987654",
            },
        }
        default.update(overrides)
        return default

    def test_happy_path_returns_probeinfo(self) -> None:
        info = _narrow_probe(self._raw())
        assert isinstance(info, ProbeInfo)
        assert info.duration == pytest.approx(12.345)
        assert info.width == 1920
        assert info.height == 1080
        assert info.video_codec == "h264"
        assert info.audio_codec == "aac"
        assert info.fps == pytest.approx(30000 / 1001)
        assert info.size_bytes == 987654

    def test_integer_fps_rational(self) -> None:
        raw = self._raw()
        raw["streams"][0]["r_frame_rate"] = "25/1"
        assert _narrow_probe(raw).fps == pytest.approx(25.0)

    def test_missing_audio_stream(self) -> None:
        raw = self._raw()
        raw["streams"] = [raw["streams"][0]]
        info = _narrow_probe(raw)
        assert info.video_codec == "h264"
        assert info.audio_codec is None

    def test_missing_video_stream(self) -> None:
        raw = self._raw()
        raw["streams"] = [raw["streams"][1]]
        info = _narrow_probe(raw)
        assert info.audio_codec == "aac"
        assert info.video_codec is None
        assert info.width is None
        assert info.height is None
        assert info.fps is None

    def test_empty_input(self) -> None:
        info = _narrow_probe({})
        assert info.duration is None
        assert info.width is None
        assert info.fps is None
        assert info.size_bytes is None

    def test_zero_denominator_fps_tolerated(self) -> None:
        raw = self._raw()
        raw["streams"][0]["r_frame_rate"] = "30/0"
        assert _narrow_probe(raw).fps is None

    def test_malformed_fps_tolerated(self) -> None:
        raw = self._raw()
        raw["streams"][0]["r_frame_rate"] = "not-a-rational"
        assert _narrow_probe(raw).fps is None
