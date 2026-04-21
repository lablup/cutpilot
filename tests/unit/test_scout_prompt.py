"""Unit tests for `_build_transcript_prompt` — the text-scout prompt renderer.

Prompt shape drifts silently; if the LLM starts getting a different prompt,
its output characteristics change. Locking shape in tests catches accidental
regressions from refactors.
"""

from __future__ import annotations

from pathlib import Path

from cutpilot.agents.scout import _build_transcript_prompt
from cutpilot.models import Transcript, TranscriptSegment, WindowAnalysis


def _seg(text: str, start: float, end: float) -> TranscriptSegment:
    return TranscriptSegment(text=text, start=start, end=end, words=[])


def _transcript() -> Transcript:
    return Transcript(
        source_path=Path("/tmp/x.mp4"),
        language="en",
        duration=600.0,
        segments=[
            _seg("Intro to the talk.", 0.0, 30.0),
            _seg("The key argument.", 30.0, 120.0),
            _seg("Demo of the system.", 120.0, 300.0),
        ],
    )


class TestBuildTranscriptPrompt:
    def test_transcript_only(self) -> None:
        prompt = _build_transcript_prompt(_transcript(), windows=None)
        assert "Video duration: 600.0 seconds." in prompt
        assert "Transcript (segment timestamps in seconds):" in prompt
        assert "[0.0-30.0] Intro to the talk." in prompt
        assert "[30.0-120.0] The key argument." in prompt
        assert "[120.0-300.0] Demo of the system." in prompt
        assert "Visual observations" not in prompt  # no VL section
        assert "CandidatesResult schema" in prompt

    def test_transcript_with_windows(self) -> None:
        windows = [
            WindowAnalysis(start_ts=0.0, end_ts=60.0, visual_score=4, visual_hook="Speaker opens with slide"),
            WindowAnalysis(start_ts=120.0, end_ts=180.0, visual_score=2, visual_hook="Wide shot, mostly text"),
        ]
        prompt = _build_transcript_prompt(_transcript(), windows=windows)
        assert "Visual observations" in prompt
        assert "[0.0-60.0] visual_score=4 | Speaker opens with slide" in prompt
        assert "[120.0-180.0] visual_score=2 | Wide shot, mostly text" in prompt
        # Transcript still present.
        assert "[0.0-30.0] Intro to the talk." in prompt

    def test_empty_segments_filtered_out(self) -> None:
        t = Transcript(
            source_path=Path("/tmp/x.mp4"),
            language="en",
            duration=10.0,
            segments=[_seg("", 0.0, 5.0), _seg("kept", 5.0, 10.0)],
        )
        prompt = _build_transcript_prompt(t, windows=None)
        # Empty-text segment must be filtered; only 'kept' shows up.
        assert "[0.0-5.0]" not in prompt
        assert "[5.0-10.0] kept" in prompt

    def test_windows_preserve_order(self) -> None:
        # Caller is responsible for ordering; we must not re-sort.
        windows = [
            WindowAnalysis(start_ts=500.0, end_ts=560.0, visual_score=5, visual_hook="late"),
            WindowAnalysis(start_ts=100.0, end_ts=160.0, visual_score=3, visual_hook="early"),
        ]
        prompt = _build_transcript_prompt(_transcript(), windows=windows)
        late_idx = prompt.index("late")
        early_idx = prompt.index("early")
        assert late_idx < early_idx  # i.e. the caller-given order is preserved
