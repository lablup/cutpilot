"""Load/save domain objects ↔ disk. Uses Pydantic JSON roundtrip — no manual dict plumbing."""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def save(obj: BaseModel, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj.model_dump_json(indent=2))


def load(model_cls: type[T], path: Path) -> T:
    return model_cls.model_validate_json(path.read_text())
