"""Unit tests for `cutpilot.settings` — the env-var contract."""

from __future__ import annotations

from cutpilot.settings import Settings, settings


def test_defaults_present() -> None:
    # Values come from .env in the dev env; fields we care about must always be strings.
    assert isinstance(settings.nim_vl_base_url, str)
    assert isinstance(settings.nim_vl_model, str)
    assert isinstance(settings.nim_text_base_url, str)
    assert isinstance(settings.whisper_base_url, str)
    assert settings.whisper_language  # non-empty


def test_env_override(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("NIM_VL_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("NIM_VL_MODEL", "model-x")
    fresh = Settings()
    assert fresh.nim_vl_base_url == "https://example.test/v1"
    assert fresh.nim_vl_model == "model-x"


def test_extra_env_vars_ignored(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CUTPILOT_TOTALLY_UNKNOWN", "whatever")
    # `extra="ignore"` in SettingsConfigDict — must not raise.
    Settings()
