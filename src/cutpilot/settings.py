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
    nim_text_model: str = "nvidia/nemotron-3-nano-30b-a3b"

    # --- NVIDIA NIM: vision-language (Scout + on-demand frame analysis) ---
    nim_vl_base_url: str = "http://0.0.0.0:9000/v1"
    nim_vl_model: str = "nvidia/nemotron-nano-12b-v2-vl"

    # --- NVIDIA Riva: Whisper-Large ASR ---
    riva_server: str = "0.0.0.0:8100"
    whisper_language: str = "en-US"

    # --- Credentials ---
    nvidia_api_key: str = ""
    ngc_api_key: str = ""

    # --- Paths (resolved via paths.py, never manipulated inline) ---
    cutpilot_sources_dir: Path = Field(default=Path("./sources"))
    cutpilot_work_dir: Path = Field(default=Path("./work"))
    cutpilot_outputs_dir: Path = Field(default=Path("./outputs"))


settings = Settings()
