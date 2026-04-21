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


async def probe_duration(source: Path) -> float:
    """Return the video's total duration in seconds via `ffprobe`."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(source),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed ({proc.returncode}): {stderr.decode(errors='replace')}")
    return float(stdout.decode().strip())


async def prepare_video_for_vl(source: Path, output: Path, height: int = 480, crf: int = 30) -> None:
    """Transcode the source to a compact muxed mp4 suitable for base64 inlining in a VL call.

    The Nemotron VL NIM samples frames server-side, so we just need *a* video
    small enough to fit in one HTTP POST after base64 encoding. Defaults produce
    roughly 30–60 MB for a 45-minute source — tune `crf` up or `height` down
    if the NIM rejects the payload.

    Drops audio (`-an`) since the VL NIM ignores it.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    await _run([
        "-i", str(source),
        "-an",
        "-vf", f"scale=-2:{height}",
        "-c:v", "libx264", "-preset", "fast", "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output),
    ])


async def extract_frames(
    source: Path,
    out_dir: Path,
    fps_target: float,
    max_frames: int,
) -> list[tuple[float, Path]]:
    """Extract uniformly-sampled JPEG frames spanning the full video.

    The effective sampling rate is chosen so the output is exactly N frames
    evenly distributed across `[0, duration]`, where N is capped at `max_frames`.

    Returns: list of `(timestamp_seconds, jpeg_path)` tuples in playback order.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = await probe_duration(source)
    n_frames = max(8, min(max_frames, int(duration * fps_target)))
    effective_fps = n_frames / duration
    await _run([
        "-i", str(source),
        "-vf", f"fps={effective_fps}",
        "-frames:v", str(n_frames),
        "-q:v", "3",
        str(out_dir / "frame_%06d.jpg"),
    ])
    return [
        ((i - 0.5) / effective_fps, out_dir / f"frame_{i:06d}.jpg")
        for i in range(1, n_frames + 1)
        if (out_dir / f"frame_{i:06d}.jpg").exists()
    ]


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
