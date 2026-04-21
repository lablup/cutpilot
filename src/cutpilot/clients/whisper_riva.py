"""Whisper-Large ASR via NVIDIA Riva.

The Riva NIM endpoint is gRPC (not OpenAI-compatible), so this does not go through
NAT's `llms:` block — it's a perception-stage client called from `pipeline.py`.

Reference: https://github.com/nvidia-riva/python-clients
    python3 python-clients/scripts/asr/transcribe_file_offline.py \
        --server 0.0.0.0:8100 --language-code en-US --input-file <audio.wav>
"""

from __future__ import annotations

from pathlib import Path

import structlog

from cutpilot.models import Transcript, TranscriptSegment, Word
from cutpilot.settings import settings

log = structlog.get_logger()


async def transcribe(audio_path: Path, source_path: Path) -> Transcript:
    """Offline transcription of a WAV file. Returns word-level timestamped Transcript.

    TODO: wire up `riva.client.ASRService.offline_recognize(...)` once the Riva
    endpoint is reachable from the dev machine. The shape below mirrors what
    `transcribe_file_offline.py` emits (SpeechRecognitionAlternative with word_info).
    """
    import riva.client  # noqa: F401 — stub import to pin the dep

    log.info(
        "whisper_riva.transcribe.start",
        server=settings.riva_server,
        language=settings.whisper_language,
        audio=str(audio_path),
    )
    raise NotImplementedError(
        "Wire riva.client.ASRService.offline_recognize once the Riva endpoint is up."
    )


def _segments_from_riva_response(response: object) -> list[TranscriptSegment]:
    """Map Riva's SpeechRecognitionResult → our TranscriptSegment list.

    Kept separate from the I/O path so it's unit-testable with a fake response.
    """
    raise NotImplementedError


__all__ = ["transcribe", "Word", "TranscriptSegment"]
