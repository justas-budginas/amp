from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    """Raised when required bot configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str
    application_id: int | None = None
    test_guild_id: int | None = None
    log_level: str = "INFO"
    idle_disconnect_seconds: int = 300
    max_queue_size: int = 100
    extraction_timeout_seconds: int = 45
    command_cooldown_seconds: int = 3
    ffmpeg_path: str = "ffmpeg"


def _optional_int(name: str, value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return parsed


def _positive_int(name: str, value: str | None, default: int) -> int:
    try:
        parsed = int(value) if value else default
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return parsed


def load_settings() -> Settings:
    load_dotenv(".env.amp")

    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise ConfigurationError("DISCORD_TOKEN is required")

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigurationError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")

    return Settings(
        discord_token=token,
        application_id=_optional_int("DISCORD_APPLICATION_ID", os.getenv("DISCORD_APPLICATION_ID")),
        test_guild_id=_optional_int("DISCORD_TEST_GUILD_ID", os.getenv("DISCORD_TEST_GUILD_ID")),
        log_level=log_level,
        idle_disconnect_seconds=_positive_int(
            "IDLE_DISCONNECT_SECONDS", os.getenv("IDLE_DISCONNECT_SECONDS"), 300
        ),
        max_queue_size=_positive_int("MAX_QUEUE_SIZE", os.getenv("MAX_QUEUE_SIZE"), 100),
        extraction_timeout_seconds=_positive_int(
            "EXTRACTION_TIMEOUT_SECONDS", os.getenv("EXTRACTION_TIMEOUT_SECONDS"), 45
        ),
        command_cooldown_seconds=_positive_int(
            "COMMAND_COOLDOWN_SECONDS", os.getenv("COMMAND_COOLDOWN_SECONDS"), 3
        ),
        ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg").strip() or "ffmpeg",
    )
