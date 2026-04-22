"""Unit tests for the Editor's SRT emitter — pure data transformation, no
ffmpeg / no NIM. Verifies the transcript-to-captions mapping used by
`burn_captions`."""

from __future__ import annotations

from pathlib import Path

import pytest

from cutpilot.agents.editor import _fmt_srt_ts, _transcript_to_srt
from cutpilot.models import Transcript, TranscriptSegment


def _seg(text: str, start: float, end: float) -> TranscriptSegment:
    return TranscriptSegment(text=text, start=start, end=end, words=[])


def _transcript(segments: list[TranscriptSegment], duration: float = 120.0) -> Transcript:
    return Transcript(
        source_path=Path("/tmp/fake.mp4"),
        language="en",
        duration=duration,
        segments=segments,
    )


def test_fmt_srt_ts_zero() -> None:
    assert _fmt_srt_ts(0.0) == "00:00:00,000"


def test_fmt_srt_ts_milliseconds() -> None:
    assert _fmt_srt_ts(1.234) == "00:00:01,234"


def test_fmt_srt_ts_hours_minutes_seconds() -> None:
    assert _fmt_srt_ts(3725.5) == "01:02:05,500"


def test_fmt_srt_ts_negative_clamped_to_zero() -> None:
    assert _fmt_srt_ts(-3.0) == "00:00:00,000"


def test_transcript_to_srt_empty_window() -> None:
    t = _transcript([_seg("Hello.", 0.0, 2.0)])
    srt, body = _transcript_to_srt(t, [(10.0, 20.0)])
    assert srt == ""
    assert body == ""


def test_transcript_to_srt_zeroes_timestamps_relative_to_window() -> None:
    t = _transcript([
        _seg("First segment.", 30.0, 35.0),
        _seg("Second segment.", 35.0, 40.0),
    ])
    srt, body = _transcript_to_srt(t, [(30.0, 40.0)])
    assert "00:00:00,000 --> 00:00:05,000" in srt
    assert "00:00:05,000 --> 00:00:10,000" in srt
    assert "First segment." in srt
    assert "Second segment." in srt
    assert body == "First segment. Second segment."


def test_transcript_to_srt_clips_partial_segments() -> None:
    """A segment overlapping one edge of the window is clipped to the window."""
    t = _transcript([_seg("Spans the edge.", 28.0, 33.0)])
    srt, _ = _transcript_to_srt(t, [(30.0, 35.0)])
    assert "00:00:00,000 --> 00:00:03,000" in srt


def test_transcript_to_srt_drops_empty_text_segments() -> None:
    t = _transcript([
        _seg("  ", 30.0, 32.0),
        _seg("Real text.", 32.0, 34.0),
    ])
    srt, body = _transcript_to_srt(t, [(30.0, 35.0)])
    assert "Real text." in srt
    assert body == "Real text."
    assert srt.count("-->") == 1


def test_transcript_to_srt_splice_two_ranges_cumulative_offset() -> None:
    """Captions from a later range land at cumulative offset on the spliced timeline."""
    t = _transcript([
        _seg("First idea.", 10.0, 15.0),
        _seg("Second idea.", 100.0, 105.0),
    ])
    # Splice two 10 s ranges; first range contributes captions at [0-5],
    # second range contributes captions at cumulative [10-15] (its local
    # [0-5] + the first range's 10 s duration).
    srt, body = _transcript_to_srt(t, [(10.0, 20.0), (100.0, 110.0)])
    assert "00:00:00,000 --> 00:00:05,000" in srt
    assert "00:00:10,000 --> 00:00:15,000" in srt
    assert "First idea." in srt
    assert "Second idea." in srt
    assert body == "First idea. Second idea."
    # Two blocks, numbered 1 and 2.
    assert srt.startswith("1\n")
    assert "\n2\n" in srt
