"""Unit tests for `cutpilot.models` — the Pydantic SSoT for cross-boundary data."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from cutpilot.models import (
    Candidate,
    CandidatesResult,
    ClipManifest,
    RubricScores,
    Transcript,
    TranscriptSegment,
    Word,
)


class TestWord:
    def test_basic(self) -> None:
        w = Word(text="hello", start=0.0, end=0.5)
        assert w.text == "hello"
        assert w.end > w.start

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            Word(text="x", start=0.0, end=1.0, confidence=0.9)  # type: ignore[call-arg]


class TestTranscript:
    def _sample_segments(self) -> list[TranscriptSegment]:
        return [
            TranscriptSegment(
                text="hello world",
                start=0.0,
                end=1.0,
                words=[Word(text="hello", start=0.0, end=0.5), Word(text="world", start=0.5, end=1.0)],
            ),
            TranscriptSegment(text="goodbye", start=1.0, end=1.5, words=[]),
        ]

    def test_full_text_joins_segments(self) -> None:
        t = Transcript(
            source_path=Path("/tmp/x.mp4"),
            language="en",
            duration=2.0,
            segments=self._sample_segments(),
        )
        assert t.full_text == "hello world goodbye"

    def test_empty_segments_yields_empty_text(self) -> None:
        t = Transcript(source_path=Path("/tmp/x.mp4"), language="en", duration=0.0, segments=[])
        assert t.full_text == ""


class TestRubricScores:
    def test_composite_is_mean(self) -> None:
        r = RubricScores(hook=5, self_contained=3, length_fit=4, visual_fit=2)
        assert r.composite == pytest.approx((5 + 3 + 4 + 2) / 4)

    def test_bounds_enforced(self) -> None:
        with pytest.raises(ValidationError):
            RubricScores(hook=0, self_contained=1, length_fit=1, visual_fit=1)
        with pytest.raises(ValidationError):
            RubricScores(hook=6, self_contained=1, length_fit=1, visual_fit=1)

    def test_floats_rejected(self) -> None:
        # Field is `int` — Pydantic v2 coerces clean floats to int, so 3.0 is OK
        # but "3.7" is not. We keep the rubric as integers; no half-star scores.
        ok = RubricScores(hook=3.0, self_contained=1, length_fit=1, visual_fit=1)  # type: ignore[arg-type]
        assert ok.hook == 3
        with pytest.raises(ValidationError):
            RubricScores(hook=3.7, self_contained=1, length_fit=1, visual_fit=1)  # type: ignore[arg-type]


class TestCandidate:
    def _scores(self) -> RubricScores:
        return RubricScores(hook=4, self_contained=4, length_fit=4, visual_fit=4)

    def test_valid_duration(self) -> None:
        c = Candidate(start_ts=10.0, end_ts=45.0, hook="x", rationale="y", scores=self._scores())
        assert c.end_ts - c.start_ts == 35.0

    def test_end_before_start_rejected(self) -> None:
        with pytest.raises(ValidationError, match="end_ts must be greater"):
            Candidate(start_ts=50.0, end_ts=50.0, hook="x", rationale="y", scores=self._scores())

    def test_duration_below_min_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duration .* outside 20"):
            Candidate(start_ts=0.0, end_ts=19.9, hook="x", rationale="y", scores=self._scores())

    def test_duration_above_max_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duration .* outside"):
            Candidate(start_ts=0.0, end_ts=91.0, hook="x", rationale="y", scores=self._scores())

    def test_negative_start_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Candidate(start_ts=-0.1, end_ts=30.0, hook="x", rationale="y", scores=self._scores())


class TestCandidatesResult:
    def _mk(self, i: int) -> Candidate:
        return Candidate(
            start_ts=float(i * 100),
            end_ts=float(i * 100 + 25),
            hook=f"hook-{i}",
            rationale=f"rationale-{i}",
            scores=RubricScores(hook=3, self_contained=3, length_fit=3, visual_fit=3),
        )

    def test_five_accepted(self) -> None:
        r = CandidatesResult(candidates=[self._mk(i) for i in range(5)])
        assert len(r.candidates) == 5

    def test_four_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least 5"):
            CandidatesResult(candidates=[self._mk(i) for i in range(4)])

    def test_eleven_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at most 10"):
            CandidatesResult(candidates=[self._mk(i) for i in range(11)])


class TestClipManifest:
    def _scores(self) -> RubricScores:
        return RubricScores(hook=4, self_contained=4, length_fit=4, visual_fit=4)

    def test_basic(self, tmp_path: Path) -> None:
        m = ClipManifest(
            clip_index=1,
            source_path=tmp_path / "src.mp4",
            start_ts=10.0,
            end_ts=40.0,
            hook="h",
            rationale="r",
            scores=self._scores(),
            caption_text="Hello",
            output_path=tmp_path / "out.mp4",
        )
        assert m.clip_index == 1
        assert m.reasoning_trace_path is None

    def test_clip_index_out_of_range_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError):
            ClipManifest(
                clip_index=4,
                source_path=tmp_path / "s.mp4",
                start_ts=0.0,
                end_ts=30.0,
                hook="h",
                rationale="r",
                scores=self._scores(),
                caption_text="c",
                output_path=tmp_path / "o.mp4",
            )
