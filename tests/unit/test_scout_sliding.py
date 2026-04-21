"""Unit tests for `_compute_window_starts` — the pure math behind the VL scan.

This helper is responsible for evenly distributing VL-scan windows across the
source's timeline. Getting the stride / endpoint handling wrong silently skips
video ranges, which isn't caught by Pydantic or by any integration test.
"""

from __future__ import annotations

import pytest

from cutpilot.agents.scout import _compute_window_starts


class TestComputeWindowStarts:
    def test_duration_shorter_than_window(self) -> None:
        # Source shorter than window → one start at 0; caller reads a partial window.
        assert _compute_window_starts(duration=30.0, n_windows=5, window_len_s=90.0) == [0.0]

    def test_duration_equal_to_window(self) -> None:
        assert _compute_window_starts(duration=90.0, n_windows=5, window_len_s=90.0) == [0.0]

    def test_single_window(self) -> None:
        # n_windows == 1 collapses to start=0 regardless of duration.
        assert _compute_window_starts(duration=300.0, n_windows=1, window_len_s=60.0) == [0.0]

    def test_endpoints_inclusive(self) -> None:
        # First start is exactly 0, last start is exactly duration - window_len.
        starts = _compute_window_starts(duration=1000.0, n_windows=5, window_len_s=100.0)
        assert starts[0] == 0.0
        assert starts[-1] == pytest.approx(900.0)
        assert len(starts) == 5

    def test_stride_is_monotone_and_unique(self) -> None:
        starts = _compute_window_starts(duration=600.0, n_windows=10, window_len_s=60.0)
        assert len(starts) == 10
        # Strictly increasing
        for prev, nxt in zip(starts, starts[1:], strict=False):
            assert nxt > prev
        # Uniform stride (within float tolerance)
        deltas = [b - a for a, b in zip(starts, starts[1:], strict=False)]
        assert all(abs(d - deltas[0]) < 1e-9 for d in deltas)

    def test_last_window_ends_at_duration(self) -> None:
        # Given a caller that does `end = start + window_len`, the last window's
        # end must exactly equal `duration`.
        duration = 2583.0
        window_len = 90.0
        starts = _compute_window_starts(duration=duration, n_windows=15, window_len_s=window_len)
        last_end = starts[-1] + window_len
        assert last_end == pytest.approx(duration)
