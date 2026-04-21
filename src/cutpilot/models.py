"""Pydantic domain types — SSoT for anything that crosses an agent or disk boundary."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Word(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    start: float
    end: float


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    start: float
    end: float
    words: list[Word] = Field(default_factory=list)


class Transcript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: Path
    language: str
    duration: float
    segments: list[TranscriptSegment]

    @property
    def full_text(self) -> str:
        return " ".join(seg.text for seg in self.segments)


class RubricScores(BaseModel):
    """Self-scored 1–5 integers across the four sprint-scope rubric axes."""

    model_config = ConfigDict(extra="forbid")

    hook: int = Field(ge=1, le=5)
    self_contained: int = Field(ge=1, le=5)
    length_fit: int = Field(ge=1, le=5)
    visual_fit: int = Field(ge=1, le=5)

    @property
    def composite(self) -> float:
        return (self.hook + self.self_contained + self.length_fit + self.visual_fit) / 4


class Candidate(BaseModel):
    """A single Scout-proposed clip candidate."""

    model_config = ConfigDict(extra="forbid")

    start_ts: float = Field(ge=0)
    end_ts: float
    hook: str
    rationale: str
    scores: RubricScores

    @model_validator(mode="after")
    def _duration_in_range(self) -> Candidate:
        if self.end_ts <= self.start_ts:
            raise ValueError("end_ts must be greater than start_ts")
        duration = self.end_ts - self.start_ts
        if not (20.0 <= duration <= 90.0):
            raise ValueError(f"duration {duration:.1f}s outside 20–90s range")
        return self


class CandidatesResult(BaseModel):
    """Scout output. The function's return type *is* the schema."""

    model_config = ConfigDict(extra="forbid")

    candidates: list[Candidate] = Field(min_length=5, max_length=10)


class ClipManifest(BaseModel):
    """Per-clip sidecar JSON. Emitted by the Editor step after ffmpeg materialization."""

    model_config = ConfigDict(extra="forbid")

    clip_index: int = Field(ge=1, le=3)
    source_path: Path
    start_ts: float
    end_ts: float
    hook: str
    rationale: str
    scores: RubricScores
    caption_text: str
    output_path: Path
    reasoning_trace_path: Path | None = None
