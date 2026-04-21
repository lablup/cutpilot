"""Unit tests for `cutpilot.persistence` — Pydantic JSON round-trip via disk."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from cutpilot import persistence


class _ToyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: int


def test_roundtrip(tmp_path: Path) -> None:
    obj = _ToyModel(name="alpha", value=42)
    target = tmp_path / "out.json"
    persistence.save(obj, target)
    assert target.exists()

    loaded = persistence.load(_ToyModel, target)
    assert loaded == obj


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deep" / "out.json"
    persistence.save(_ToyModel(name="x", value=1), target)
    assert target.exists()


def test_load_unknown_field_rejected(tmp_path: Path) -> None:
    # Write a JSON with an extra field and confirm load raises (extra="forbid").
    target = tmp_path / "bad.json"
    target.write_text('{"name": "a", "value": 1, "stranger": true}')
    try:
        persistence.load(_ToyModel, target)
    except Exception as e:
        assert "stranger" in str(e) or "extra" in str(e).lower()
    else:
        raise AssertionError("expected ValidationError for extra field")
