"""Unit tests for the parse/repair helpers in `cutpilot.agents.scout`.

These cover the logic that converts a raw NIM response into a `CandidatesResult`
— no network, no ffmpeg, no LLM handle involved.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from cutpilot.agents.scout import (
    MAX_DURATION_S,
    MIN_ACCEPTABLE_S,
    MIN_DURATION_S,
    _parse_candidates,
    _repair_candidate,
)


def _c(start: float, end: float, *, hook: str = "h", rationale: str = "r") -> dict:
    """One raw candidate dict, shape matching the Scout JSON contract."""
    return {
        "start_ts": start,
        "end_ts": end,
        "hook": hook,
        "rationale": rationale,
        "scores": {"hook": 3, "self_contained": 3, "length_fit": 3, "visual_fit": 3},
    }


class TestRepairCandidate:
    def test_in_range_passes_through(self) -> None:
        raw = _c(10.0, 40.0)
        out = _repair_candidate(raw)
        assert out == raw

    def test_short_padded_to_minimum(self) -> None:
        raw = _c(100.0, 116.0)  # 16s
        out = _repair_candidate(raw)
        assert out is not None
        assert out["end_ts"] - out["start_ts"] == pytest.approx(MIN_DURATION_S)
        assert out["start_ts"] < 100.0
        assert out["end_ts"] > 116.0

    def test_start_clamped_at_zero(self) -> None:
        raw = _c(1.0, 18.0)  # 17s, pad would push start negative
        out = _repair_candidate(raw)
        assert out is not None
        assert out["start_ts"] == 0.0

    def test_too_short_dropped(self) -> None:
        raw = _c(0.0, MIN_ACCEPTABLE_S - 0.5)
        assert _repair_candidate(raw) is None

    def test_too_long_dropped(self) -> None:
        raw = _c(0.0, MAX_DURATION_S + 1.0)
        assert _repair_candidate(raw) is None

    def test_zero_or_negative_duration_dropped(self) -> None:
        assert _repair_candidate(_c(50.0, 50.0)) is None
        assert _repair_candidate(_c(50.0, 40.0)) is None

    def test_missing_fields_dropped(self) -> None:
        assert _repair_candidate({"end_ts": 40.0}) is None
        assert _repair_candidate({"start_ts": "garbage", "end_ts": 40.0}) is None


class TestParseCandidates:
    def test_five_valid_passes(self) -> None:
        payload = {"candidates": [_c(i * 100.0, i * 100.0 + 25.0) for i in range(5)]}
        result = _parse_candidates(json.dumps(payload))
        assert len(result.candidates) == 5

    def test_repair_and_drop_mixed(self) -> None:
        # 3 short-but-salvageable (pad), 2 fine, 2 unsalvageable = 5 survivors.
        raw = [
            _c(0.0, 16.0),     # 16s → pad to 20
            _c(50.0, 67.0),    # 17s → pad
            _c(100.0, 118.0),  # 18s → pad
            _c(200.0, 230.0),  # 30s → keep
            _c(300.0, 350.0),  # 50s → keep
            _c(400.0, 400.5),  # too short → drop
            _c(500.0, 600.5),  # too long → drop
        ]
        result = _parse_candidates(json.dumps({"candidates": raw}))
        assert len(result.candidates) == 5

    def test_all_unsalvageable_fails_closed(self) -> None:
        raw = [_c(0.0, 5.0) for _ in range(6)]  # all too short
        with pytest.raises(ValidationError, match="at least 5"):
            _parse_candidates(json.dumps({"candidates": raw}))

    def test_malformed_json_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            _parse_candidates("this is not json at all")

    def test_missing_candidates_key_is_empty_list(self) -> None:
        with pytest.raises(ValidationError, match="at least 5"):
            _parse_candidates(json.dumps({}))
