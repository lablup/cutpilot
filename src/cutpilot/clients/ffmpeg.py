"""Safe ffmpeg invocation. All ffmpeg calls go through here — no inline subprocess elsewhere."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from cutpilot.models import ProbeInfo

# ---------------------------------------------------------------------------
# Caption rendering (Pillow → PNGs composited via `overlay` filter).
# Chosen over `subtitles=` / `drawtext` because both need libass / libfreetype,
# neither of which ship in Homebrew's default ffmpeg bottle. `overlay` is a
# core filter that's always available, so this path works on any ffmpeg build.
# ---------------------------------------------------------------------------

CAPTION_FONT_CANDIDATES = [
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
]
CAPTION_FONT_SIZE = 56
CAPTION_MAX_TEXT_WIDTH = 900  # for 1080-wide video, leaves ~90 px side margins
CAPTION_BOTTOM_PAD = 220      # px above bottom edge — clears TikTok / Shorts UI
CAPTION_BG_ALPHA = 210        # black pill bg opacity (0-255)


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
    """Return the duration of `source` in seconds via ffprobe.

    Used by the Whisper client to size the synthetic segment spans when the
    NIM returns text-only responses (no native timestamps)."""
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
        raise RuntimeError(
            f"ffprobe failed ({proc.returncode}): {stderr.decode(errors='replace')}"
        )
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


async def split_audio(
    *,
    source: Path,
    chunk_seconds: int,
    output_dir: Path,
) -> list[Path]:
    """Split a WAV into fixed-length chunks via ffmpeg's segment muxer.

    Callers get back chunk paths in chronological order. Each chunk starts
    at `index * chunk_seconds` in the source, which lets the Whisper client
    recover absolute timestamps by offsetting chunk-local ones.

    `-c copy` works cleanly on PCM WAV (no inter-frame deps), so this is
    fast and drift-free.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = output_dir / "chunk_%04d.wav"
    await _run([
        "-i", str(source),
        "-f", "segment",
        "-segment_time", str(chunk_seconds),
        "-c", "copy",
        str(pattern),
    ])
    return sorted(output_dir.glob("chunk_*.wav"))


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


async def burn_captions(
    source: Path,
    segments: list[tuple[float, float, str]],
    output: Path,
    *,
    work_dir: Path | None = None,
) -> None:
    """Composite captions onto `source` as Pillow-rendered PNG overlays.

    `segments` are `(start_s, end_s, text)` tuples on the video's local
    timeline (zeroed at source's start). Each segment becomes one PNG input,
    chained through the `overlay` filter with `enable='between(t,s,e)'`.

    Requires only the core `overlay` filter — no libass or libfreetype.
    """
    if not segments:
        raise ValueError("burn_captions called with no segments — caller should skip")

    output.parent.mkdir(parents=True, exist_ok=True)
    font_path = _caption_font_path()
    scratch = work_dir if work_dir is not None else output.parent / f".cap_{output.stem}"
    scratch.mkdir(parents=True, exist_ok=True)

    png_paths: list[Path] = []
    for i, (_, _, text) in enumerate(segments):
        png_path = scratch / f"cap_{i:04d}.png"
        await asyncio.to_thread(
            _render_caption_png,
            text, png_path, font_path, CAPTION_FONT_SIZE, CAPTION_MAX_TEXT_WIDTH,
        )
        png_paths.append(png_path)

    # Build the filter_complex chain: video → overlay cap0 → overlay cap1 → …
    chain_parts: list[str] = []
    prev_label = "[0:v]"
    for i, (start, end, _) in enumerate(segments):
        input_label = f"[{i + 1}:v]"
        out_label = "[vout]" if i == len(segments) - 1 else f"[v{i}]"
        chain_parts.append(
            f"{prev_label}{input_label}overlay="
            f"x=(main_w-overlay_w)/2:y=main_h-overlay_h-{CAPTION_BOTTOM_PAD}"
            f":enable='between(t,{start:.3f},{end:.3f})'"
            f"{out_label}"
        )
        prev_label = out_label
    filter_complex = ";".join(chain_parts)

    args: list[str] = ["-i", str(source)]
    for p in png_paths:
        args += ["-i", str(p)]
    args += [
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "copy",
        str(output),
    ]
    await _run(args)


def _caption_font_path() -> str:
    for candidate in CAPTION_FONT_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    raise RuntimeError(
        "No caption font found; expected one of: "
        + ", ".join(str(c) for c in CAPTION_FONT_CANDIDATES)
    )


def _render_caption_png(
    text: str,
    out_path: Path,
    font_path: str,
    font_size: int,
    max_text_width: int,
) -> None:
    """Render one caption segment as a transparent-bg PNG with a rounded black
    pill behind white text. Word-wraps to fit within `max_text_width`."""
    font = ImageFont.truetype(font_path, font_size)
    lines = _wrap_lines(text, font, max_text_width)

    ascent, descent = font.getmetrics()
    line_h = ascent + descent
    line_gap = int(line_h * 0.15)
    pad_x, pad_y = 40, 22

    widths: list[int] = []
    for line in lines:
        left, _, right, _ = font.getbbox(line)
        widths.append(right - left)
    content_w = max(widths) if widths else 0
    content_h = (
        len(lines) * line_h + max(0, len(lines) - 1) * line_gap if lines else 0
    )
    img_w = content_w + 2 * pad_x
    img_h = content_h + 2 * pad_y

    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        (0, 0, img_w, img_h),
        radius=min(img_h // 2, 32),
        fill=(0, 0, 0, CAPTION_BG_ALPHA),
    )

    y = pad_y
    for line, w in zip(lines, widths, strict=True):
        left, _, _, _ = font.getbbox(line)
        x = (img_w - w) // 2 - left
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_h + line_gap
    img.save(out_path, format="PNG")


def _wrap_lines(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Greedy word-wrap: accumulate words until the next one would overflow."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        test = f"{current} {word}"
        left, _, right, _ = font.getbbox(test)
        if right - left > max_width:
            lines.append(current)
            current = word
        else:
            current = test
    lines.append(current)
    return lines


# ---------------------------------------------------------------------------
# Splice (concat demuxer), merge (mux A/V), save (standard export), probe.
# ---------------------------------------------------------------------------


def _format_concat_listfile(sources: list[Path]) -> str:
    """Render a concat-demuxer listfile.

    Each line is `file '<absolute path>'`. Single quotes inside paths are
    escaped per ffmpeg's concat rules (`'` → `'\\''`).
    """
    lines = [f"file '{str(src.resolve()).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for src in sources]
    return "\n".join(lines) + "\n"


async def _run_concat(sources: list[Path], extra_args: list[str], output: Path) -> None:
    """Write a temp concat listfile, invoke ffmpeg, clean up.

    `extra_args` is spliced between the `-i listfile` and the output path, so
    callers control codec flags without re-implementing the listfile dance.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    text = _format_concat_listfile(sources)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text)
        listfile_path = Path(f.name)
    try:
        await _run([
            "-f", "concat", "-safe", "0",
            "-i", str(listfile_path),
            *extra_args,
            str(output),
        ])
    finally:
        listfile_path.unlink(missing_ok=True)


async def concat_copy(sources: list[Path], output: Path) -> None:
    """Splice clips end-to-end with `-c copy` (concat demuxer). Fast but needs
    matching codec/timebase/resolution — callers should fall back to
    `concat_reencode` on RuntimeError."""
    await _run_concat(sources, ["-c", "copy"], output)


async def concat_reencode(sources: list[Path], output: Path) -> None:
    """Splice clips end-to-end with re-encode. Works across codec mismatches."""
    await _run_concat(
        sources,
        ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-c:a", "aac", "-b:a", "128k"],
        output,
    )


async def mux_av(video: Path, audio: Path, output: Path) -> None:
    """Combine a video-only and audio-only source into one MP4 with `-c copy`.

    Pins exactly one video + one audio stream via explicit `-map`, and uses
    `-shortest` so mismatched durations truncate to the shorter track instead
    of padding silently.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    await _run([
        "-i", str(video),
        "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest",
        "-c", "copy",
        str(output),
    ])


async def export_standard(source: Path, output: Path) -> None:
    """Final re-encode to a shareable MP4: libx264 preset=fast CRF=20, AAC 128k, +faststart."""
    output.parent.mkdir(parents=True, exist_ok=True)
    await _run([
        "-i", str(source),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output),
    ])


async def _run_probe(args: list[str]) -> str:
    """Invoke `ffprobe -v error <args>` and return stdout."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed ({proc.returncode}): {stderr.decode(errors='replace')}")
    return stdout.decode()


def _narrow_probe(raw: dict[str, Any]) -> ProbeInfo:
    """Narrow full ffprobe JSON to the seven fields CutPilot cares about.

    Pure function so it's unit-testable with a canned dict and does not depend
    on `ffprobe` being installed.
    """
    fmt: dict[str, Any] = raw.get("format") or {}
    streams: list[dict[str, Any]] = raw.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None) or {}
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None) or {}

    fps: float | None = None
    r_frame_rate = video.get("r_frame_rate")
    if isinstance(r_frame_rate, str) and "/" in r_frame_rate:
        num_str, _, den_str = r_frame_rate.partition("/")
        try:
            num, den = float(num_str), float(den_str)
            fps = num / den if den else None
        except ValueError:
            fps = None

    duration = fmt.get("duration")
    width = video.get("width")
    height = video.get("height")
    size = fmt.get("size")

    return ProbeInfo(
        duration=float(duration) if duration is not None else None,
        width=int(width) if width is not None else None,
        height=int(height) if height is not None else None,
        video_codec=video.get("codec_name"),
        audio_codec=audio.get("codec_name"),
        fps=fps,
        size_bytes=int(size) if size is not None else None,
    )


async def probe_media(source: Path) -> ProbeInfo:
    """Inspect a media file via `ffprobe -show_streams -show_format -print_format json`."""
    stdout = await _run_probe([
        "-show_streams", "-show_format",
        "-print_format", "json",
        str(source),
    ])
    return _narrow_probe(json.loads(stdout))
