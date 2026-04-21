"""Unit tests for `WindowAnalysis` — the VL scan's per-window payload."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cutpilot.models import WindowAnalysis


class TestWindowAnalysis:
    def test_basic(self) -> None:
        w = WindowAnalysis(start_ts=0.0, end_ts=60.0, visual_score=3, visual_hook="ok")
        assert w.visual_score == 3
        assert w.visual_hook == "ok"

    def test_visual_score_bounds(self) -> None:
        with pytest.raises(ValidationError):
            WindowAnalysis(start_ts=0.0, end_ts=60.0, visual_score=0, visual_hook="x")
        with pytest.raises(ValidationError):
            WindowAnalysis(start_ts=0.0, end_ts=60.0, visual_score=6, visual_hook="x")

    def test_negative_start_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WindowAnalysis(start_ts=-1.0, end_ts=60.0, visual_score=3, visual_hook="x")

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            WindowAnalysis(
                start_ts=0.0, end_ts=60.0, visual_score=3, visual_hook="x",
                stranger=True,  # type: ignore[call-arg]
            )
