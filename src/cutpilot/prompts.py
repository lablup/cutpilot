"""Loader for `prompts/*.md`. Never inline system prompts in agent code."""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def load(name: str) -> str:
    """Read `prompts/<name>.md` from the repo root.

    Raises FileNotFoundError if the prompt does not exist — prompts are load-bearing.
    """
    path = _PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")
