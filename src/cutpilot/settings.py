"""SSoT for all config reads. Everything else imports `settings` from here."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed environment contract. Mirrors `.env.example`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- NVIDIA NIM: text reasoning (Editor) ---
    nim_text_base_url: str = "http://0.0.0.0:8000/v1"
    nim_text_model: str = "nvidia/nemotron-3-nano"

    # --- NVIDIA NIM: vision-language (Scout + on-demand frame analysis) ---
    nim_vl_base_url: str = "http://0.0.0.0:9000/v1"
    nim_vl_model: str = "nvidia/nemotron-nano-12b-v2-vl"

    # --- NVIDIA NIM: Whisper-Large ASR (OpenAI-compatible /v1/audio/transcriptions) ---
    whisper_base_url: str = "http://0.0.0.0:8100/v1"
    # Empty default → omit `model` from the transcription request. Riva-style
    # Whisper NIMs (API v1.5.1) reject explicit model names and pick their
    # loaded default. Set WHISPER_MODEL only when your NIM build requires it.
    whisper_model: str = ""
    whisper_language: str = "en"  # ISO-639-1, as OpenAI audio API expects
    # The NIM rejects audio above its internal cap with `400 audio too long`; we
    # pre-split into chunks of this length (seconds) and stitch the transcripts
    # back together. 300s is safe for all current Whisper-Large NIM builds.
    whisper_max_chunk_seconds: int = 300
    # `json` returns `{text: "..."}` only and is supported by every NIM build.
    # `verbose_json` adds segment/word timestamps but is unsupported by the
    # minimal Whisper-Large NIM. Override to `verbose_json` if your deployment
    # supports it (the client shape-detects segments regardless).
    whisper_response_format: str = "json"

    # --- Credentials ---
    nvidia_api_key: str = ""
    ngc_api_key: str = ""

    # --- Paths (resolved via paths.py, never manipulated inline) ---
    cutpilot_sources_dir: Path = Field(default=Path("./sources"))
    cutpilot_work_dir: Path = Field(default=Path("./work"))
    cutpilot_outputs_dir: Path = Field(default=Path("./outputs"))


settings = Settings()
