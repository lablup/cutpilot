"""SSoT for path computation. No `pathlib` math anywhere else in the codebase."""

from __future__ import annotations

from pathlib import Path

from cutpilot.settings import settings


def sources_dir() -> Path:
    return settings.cutpilot_sources_dir.resolve()


def work_dir(run_id: str) -> Path:
    return (settings.cutpilot_work_dir / run_id).resolve()


def run_outputs_dir(run_id: str) -> Path:
    return (settings.cutpilot_outputs_dir / run_id).resolve()


def source_video_path(run_id: str) -> Path:
    """Local landing path for a remotely-fetched source (YouTube etc.).

    The pipeline writes yt-dlp output here; `merge_output_format=mp4` keeps
    the extension stable. Local-file sources do NOT use this path."""
    return work_dir(run_id) / "source.mp4"


def audio_wav_path(run_id: str) -> Path:
    return work_dir(run_id) / "audio.wav"


def whisper_chunks_dir(run_id: str) -> Path:
    """Where `split_audio` lands per-chunk WAVs before Whisper transcribes them."""
    return work_dir(run_id) / "whisper_chunks"


def transcript_json_path(run_id: str) -> Path:
    return work_dir(run_id) / "transcript.json"


def candidates_json_path(run_id: str) -> Path:
    return work_dir(run_id) / "candidates.json"


def clip_path(run_id: str, clip_index: int) -> Path:
    return run_outputs_dir(run_id) / f"clip_{clip_index}.mp4"


def clip_manifest_path(run_id: str, clip_index: int) -> Path:
    return run_outputs_dir(run_id) / f"clip_{clip_index}.manifest.json"


def reasoning_trace_path(run_id: str) -> Path:
    return run_outputs_dir(run_id) / "reasoning_trace.jsonl"


def review_html_path(run_id: str) -> Path:
    return run_outputs_dir(run_id) / "review.html"


def ensure_dirs(run_id: str) -> None:
    work_dir(run_id).mkdir(parents=True, exist_ok=True)
    run_outputs_dir(run_id).mkdir(parents=True, exist_ok=True)
