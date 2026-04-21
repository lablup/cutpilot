"""Whisper-Large ASR via NVIDIA NIM (OpenAI-compatible /v1/audio/transcriptions).

The endpoint is a drop-in OpenAI audio API, so we use the async OpenAI SDK
against `$WHISPER_BASE_URL`. Whisper is not a chat model — it lives outside the
NAT `llms:` block and is called directly from `pipeline.py`.

Two constraints shape this module:

1. The NIM rejects audio above its internal cap with `400 audio too long`. We
   always pre-split into `settings.whisper_max_chunk_seconds` chunks,
   transcribe each, and stitch them into one Transcript.

2. Different NIM builds expose different `response_format` values. The minimal
   Whisper-Large NIM only supports `json` / `text` (no timestamps). Richer
   builds support `verbose_json` (segment + word timestamps). The client
   requests whatever `settings.whisper_response_format` says, and the response
   mapper shape-detects so either path yields a valid Transcript:

   - `json`:         one coarse TranscriptSegment per chunk, spanning the
                     chunk's actual duration (via ffprobe), no words.
   - `verbose_json`: per-segment timestamps and per-word Word objects,
                     all offset by the chunk's start time for source-relative
                     coordinates.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from openai import AsyncOpenAI

from cutpilot.clients.ffmpeg import probe_duration, split_audio
from cutpilot.models import Transcript, TranscriptSegment, Word
from cutpilot.settings import settings

log = structlog.get_logger()


async def transcribe(
    *,
    audio_path: Path,
    source_path: Path,
    chunks_dir: Path,
) -> Transcript:
    """Offline transcription of a WAV file, with transparent chunking.

    Args:
        audio_path: Path to a 16 kHz mono WAV on local disk.
        source_path: The original video path — stored on the Transcript so
            downstream tools can re-open the source.
        chunks_dir: Scratch directory for per-chunk WAVs (caller-owned, so
            cleanup policy stays with the pipeline).

    Returns:
        A validated Transcript with absolute (source-relative) timestamps.
        Raises `pydantic.ValidationError` if a chunk response cannot be
        mapped to the schema.
    """
    chunk_seconds = settings.whisper_max_chunk_seconds
    chunk_paths = await split_audio(
        source=audio_path,
        chunk_seconds=chunk_seconds,
        output_dir=chunks_dir,
    )
    if not chunk_paths:
        raise RuntimeError(f"split_audio produced no chunks for {audio_path}")

    client = AsyncOpenAI(
        base_url=settings.whisper_base_url,
        api_key=settings.nvidia_api_key or "dummy",
    )
    log.info(
        "whisper.transcribe.start",
        base_url=settings.whisper_base_url,
        model=settings.whisper_model,
        language=settings.whisper_language,
        response_format=settings.whisper_response_format,
        audio=str(audio_path),
        chunks=len(chunk_paths),
        chunk_seconds=chunk_seconds,
    )

    all_segments: list[TranscriptSegment] = []
    detected_language = settings.whisper_language
    for index, chunk_path in enumerate(chunk_paths):
        offset = float(index * chunk_seconds)
        chunk_duration = await probe_duration(chunk_path)
        response = await _transcribe_one(client=client, chunk_path=chunk_path)
        chunk_segments = _segments_from_response(
            response,
            time_offset=offset,
            fallback_duration=chunk_duration,
        )
        all_segments.extend(chunk_segments)
        chunk_language = getattr(response, "language", None)
        if chunk_language:
            detected_language = chunk_language
        log.info(
            "whisper.transcribe.chunk_done",
            chunk_index=index,
            offset_seconds=offset,
            chunk_duration=chunk_duration,
            segments=len(chunk_segments),
        )

    total_duration = await probe_duration(audio_path)
    transcript = Transcript(
        source_path=source_path,
        language=detected_language,
        duration=total_duration,
        segments=all_segments,
    )
    log.info(
        "whisper.transcribe.done",
        segments=len(all_segments),
        duration_seconds=total_duration,
    )
    return transcript


async def _transcribe_one(*, client: AsyncOpenAI, chunk_path: Path) -> Any:
    """One NIM round-trip for a single chunk. Kept thin so retry/backoff can
    wrap here in future without reshaping the outer loop.

    `timestamp_granularities` is only a valid parameter when the server supports
    `verbose_json`; sending it alongside `json` would be ignored by OpenAI but
    may 400 on strict NIMs, so we omit it in the non-verbose path.
    """
    response_format = settings.whisper_response_format
    with chunk_path.open("rb") as chunk_file:
        if response_format == "verbose_json":
            return await client.audio.transcriptions.create(
                model=settings.whisper_model,
                file=chunk_file,
                language=settings.whisper_language,
                response_format="verbose_json",
                timestamp_granularities=["word", "segment"],
            )
        return await client.audio.transcriptions.create(
            model=settings.whisper_model,
            file=chunk_file,
            language=settings.whisper_language,
            response_format=response_format,
        )


def _segments_from_response(
    response: object,
    *,
    time_offset: float,
    fallback_duration: float,
) -> list[TranscriptSegment]:
    """Map any supported transcription response → TranscriptSegment list.

    Shape-detects: if the response carries a `segments` array (verbose_json),
    we preserve per-segment and per-word timestamps, all offset by
    `time_offset` so chunked transcripts stitch into one source-relative
    timeline. Otherwise, we synthesize a single segment spanning the chunk's
    actual duration — the only timing signal available from a json/text NIM.
    """
    raw_segments = getattr(response, "segments", None)
    if raw_segments:
        return _segments_from_verbose(
            raw_segments=raw_segments,
            raw_words=getattr(response, "words", None) or [],
            time_offset=time_offset,
        )

    text = (getattr(response, "text", "") or "").strip()
    if not text:
        return []
    return [
        TranscriptSegment(
            text=text,
            start=time_offset,
            end=time_offset + fallback_duration,
            words=[],
        )
    ]


def _segments_from_verbose(
    *,
    raw_segments: list[Any],
    raw_words: list[Any],
    time_offset: float,
) -> list[TranscriptSegment]:
    """verbose_json path — one TranscriptSegment per API segment, with any
    words whose [start, end] falls inside that segment's raw (chunk-local)
    bounds attached. Both sides of the word/segment comparison use raw
    coordinates; only the emitted TranscriptSegment/Word values are offset."""
    segments: list[TranscriptSegment] = []
    for seg in raw_segments:
        raw_start = float(seg.start)
        raw_end = float(seg.end)
        words = [
            Word(
                text=word.word,
                start=float(word.start) + time_offset,
                end=float(word.end) + time_offset,
            )
            for word in raw_words
            if float(word.start) >= raw_start and float(word.end) <= raw_end
        ]
        segments.append(
            TranscriptSegment(
                text=seg.text,
                start=raw_start + time_offset,
                end=raw_end + time_offset,
                words=words,
            )
        )
    return segments
