"""Unit tests for `cutpilot.paths` — the only place pathlib math is allowed."""

from __future__ import annotations

from pathlib import Path

import pytest

from cutpilot import paths
from cutpilot.settings import settings


@pytest.fixture
def tmp_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point all three CUTPILOT_*_DIR settings at a tmp root for the test."""
    monkeypatch.setattr(settings, "cutpilot_sources_dir", tmp_path / "sources")
    monkeypatch.setattr(settings, "cutpilot_work_dir", tmp_path / "work")
    monkeypatch.setattr(settings, "cutpilot_outputs_dir", tmp_path / "outputs")
    return tmp_path


def test_work_dir_namespaced_by_run_id(tmp_dirs: Path) -> None:
    assert paths.work_dir("run-a") == (tmp_dirs / "work" / "run-a").resolve()
    assert paths.work_dir("run-b") != paths.work_dir("run-a")


def test_run_outputs_dir(tmp_dirs: Path) -> None:
    assert paths.run_outputs_dir("r1") == (tmp_dirs / "outputs" / "r1").resolve()


def test_stable_artifact_paths(tmp_dirs: Path) -> None:
    rid = "fixed"
    assert paths.audio_wav_path(rid).name == "audio.wav"
    assert paths.transcript_json_path(rid).name == "transcript.json"
    assert paths.candidates_json_path(rid).name == "candidates.json"
    assert paths.frames_dir(rid).name == "frames"
    assert paths.vl_video_path(rid).name == "video_vl.mp4"
    assert paths.reasoning_trace_path(rid).name == "reasoning_trace.jsonl"
    assert paths.review_html_path(rid).name == "review.html"


def test_clip_paths_indexed(tmp_dirs: Path) -> None:
    assert paths.clip_path("r", 1).name == "clip_1.mp4"
    assert paths.clip_manifest_path("r", 3).name == "clip_3.manifest.json"


def test_ensure_dirs_creates_work_and_outputs(tmp_dirs: Path) -> None:
    rid = "fresh"
    assert not paths.work_dir(rid).exists()
    paths.ensure_dirs(rid)
    assert paths.work_dir(rid).is_dir()
    assert paths.run_outputs_dir(rid).is_dir()


def test_ensure_dirs_idempotent(tmp_dirs: Path) -> None:
    paths.ensure_dirs("twice")
    paths.ensure_dirs("twice")  # must not raise
