"""Safe ffmpeg invocation. All ffmpeg calls go through here — no inline subprocess elsewhere."""

from __future__ import annotations

import asyncio
from pathlib import Path


async def _run(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {stderr.decode(errors='replace')}")


async def extract_audio(source: Path, output: Path) -> None:
    """Demux audio to 16 kHz mono WAV — the rate Whisper expects."""
    output.parent.mkdir(parents=True, exist_ok=True)
    await _run([
        "-i", str(source),
        "-vn", "-ac", "1", "-ar", "16000",
        str(output),
    ])


async def cut_copy(source: Path, start: float, end: float, output: Path) -> None:
    """Time-range extract with `-c copy`. Fast but can drift on non-keyframe boundaries —
    callers should fall back to `cut_reencode` if they detect artifacts."""
    output.parent.mkdir(parents=True, exist_ok=True)
    await _run([
        "-ss", f"{start:.3f}",
        "-to", f"{end:.3f}",
        "-i", str(source),
        "-c", "copy",
        str(output),
    ])


async def cut_reencode(source: Path, start: float, end: float, output: Path) -> None:
    """Frame-accurate cut via re-encode. Slower, reliable."""
    output.parent.mkdir(parents=True, exist_ok=True)
    await _run([
        "-ss", f"{start:.3f}",
        "-to", f"{end:.3f}",
        "-i", str(source),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        str(output),
    ])


async def crop_9_16_center(source: Path, output: Path) -> None:
    """Center-crop horizontal source to 1080×1920 vertical."""
    output.parent.mkdir(parents=True, exist_ok=True)
    await _run([
        "-i", str(source),
        "-vf", "crop=ih*9/16:ih,scale=1080:1920",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "copy",
        str(output),
    ])


async def burn_captions(source: Path, subtitle_file: Path, output: Path) -> None:
    """Burn subtitles from an .srt file into the video. Full-segment captions."""
    output.parent.mkdir(parents=True, exist_ok=True)
    # ffmpeg subtitles filter requires the path to be shell-escaped inside the filter string
    escaped = str(subtitle_file).replace(":", r"\:").replace("'", r"\'")
    await _run([
        "-i", str(source),
        "-vf", f"subtitles='{escaped}'",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "copy",
        str(output),
    ])
