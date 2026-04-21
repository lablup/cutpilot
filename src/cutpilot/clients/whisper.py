"""Whisper-Large ASR via NVIDIA NIM (OpenAI-compatible /v1/audio/transcriptions).

The endpoint is a drop-in OpenAI audio API, so we use the async OpenAI SDK
against `$WHISPER_BASE_URL`. Whisper is not a chat model — it lives outside the
NAT `llms:` block and is called directly from `pipeline.py`.

Word-level timestamps come from `response_format=verbose_json` with
`timestamp_granularities=["word"]`. Segment-level timestamps are always
populated; word-level depends on the NIM build — we request them and fall
back gracefully when the response omits the `words` array.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from openai import AsyncOpenAI

from cutpilot.models import Transcript, TranscriptSegment, Word
from cutpilot.settings import settings

log = structlog.get_logger()


async def transcribe(audio_path: Path, source_path: Path) -> Transcript:
    """Offline transcription of a WAV file. Returns word-level timestamped Transcript.

    Args:
        audio_path: Path to a 16 kHz mono WAV on local disk.
        source_path: The original video path — stored on the Transcript so
            downstream tools can re-open the source.

    Returns:
        A validated Transcript. Raises `pydantic.ValidationError` if the NIM
        response cannot be mapped to the schema.
    """
    client = AsyncOpenAI(
        base_url=settings.whisper_base_url,
        api_key=settings.nvidia_api_key or "dummy",
    )
    log.info(
        "whisper.transcribe.start",
        base_url=settings.whisper_base_url,
        model=settings.whisper_model,
        language=settings.whisper_language,
        audio=str(audio_path),
    )

    with audio_path.open("rb") as f:
        response = await client.audio.transcriptions.create(
            model=settings.whisper_model,
            file=f,
            language=settings.whisper_language,
            response_format="verbose_json",
            timestamp_granularities=["word", "segment"],
        )

    segments = _segments_from_response(response)
    transcript = Transcript(
        source_path=source_path,
        language=getattr(response, "language", settings.whisper_language),
        duration=float(getattr(response, "duration", 0.0)),
        segments=segments,
    )
    log.info("whisper.transcribe.done", segments=len(segments))
    return transcript


def _segments_from_response(response: object) -> list[TranscriptSegment]:
    """Map an OpenAI verbose_json transcription response → TranscriptSegment list.

    Kept separate from the I/O path so it's unit-testable with a fake response.
    Words are attached to whichever segment their [start, end] falls inside.
    """
    raw_segments = getattr(response, "segments", None) or []
    raw_words = getattr(response, "words", None) or []

    segments: list[TranscriptSegment] = []
    for seg in raw_segments:
        seg_start = float(seg.start)
        seg_end = float(seg.end)
        words = [
            Word(text=w.word, start=float(w.start), end=float(w.end))
            for w in raw_words
            if float(w.start) >= seg_start and float(w.end) <= seg_end
        ]
        segments.append(
            TranscriptSegment(
                text=seg.text,
                start=seg_start,
                end=seg_end,
                words=words,
            )
        )
    return segments
