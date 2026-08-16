import pytest

from discord_music_bot.config import ConfigurationError, load_settings


def test_load_settings_uses_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("discord_music_bot.config.load_dotenv", lambda dotenv_path: None)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    for name in (
        "DISCORD_APPLICATION_ID",
        "DISCORD_TEST_GUILD_ID",
        "LOG_LEVEL",
        "IDLE_DISCONNECT_SECONDS",
        "MAX_QUEUE_SIZE",
        "EXTRACTION_TIMEOUT_SECONDS",
        "COMMAND_COOLDOWN_SECONDS",
        "FFMPEG_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert settings.discord_token == "token"
    assert settings.max_queue_size == 100
    assert settings.ffmpeg_path == "ffmpeg"


def test_load_settings_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("discord_music_bot.config.load_dotenv", lambda dotenv_path: None)
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)

    with pytest.raises(ConfigurationError, match="DISCORD_TOKEN"):
        load_settings()


def test_load_settings_rejects_invalid_queue_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("discord_music_bot.config.load_dotenv", lambda dotenv_path: None)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("MAX_QUEUE_SIZE", "0")

    with pytest.raises(ConfigurationError, match="MAX_QUEUE_SIZE"):
        load_settings()
