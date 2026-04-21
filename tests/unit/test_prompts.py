"""Unit tests for `cutpilot.prompts` — loads markdown prompts from the repo root."""

from __future__ import annotations

from cutpilot import prompts


def test_scout_prompt_loads() -> None:
    text = prompts.load("scout")
    assert isinstance(text, str)
    assert len(text) > 100
    # Spot-check load-bearing keywords in the Scout prompt.
    assert "CandidatesResult" in text
    assert "20" in text and "90" in text  # duration bounds


def test_editor_prompt_loads() -> None:
    text = prompts.load("editor")
    assert isinstance(text, str)
    assert len(text) > 100
