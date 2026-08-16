from __future__ import annotations

import argparse
import logging
import shutil

import discord
from discord.ext import commands

from .commands.music import MusicCog
from .config import Settings, load_settings
from .music.manager import MusicManager

logger = logging.getLogger(__name__)


class MusicBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        if settings.application_id is None:
            super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        else:
            super().__init__(
                command_prefix=commands.when_mentioned,
                intents=intents,
                application_id=settings.application_id,
            )
        self.settings = settings
        self.manager = MusicManager(settings)

    async def setup_hook(self) -> None:
        await self.add_cog(MusicCog(self.manager, self.settings))
        if self.settings.test_guild_id:
            guild = discord.Object(id=self.settings.test_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Synchronized commands to test guild %s", self.settings.test_guild_id)
        else:
            await self.tree.sync()
            logger.info("Synchronized global commands")

    async def on_ready(self) -> None:
        if self.user is not None:
            logger.info("Logged in as %s (%s)", self.user, self.user.id)

    async def close(self) -> None:
        await self.manager.shutdown()
        await super().close()


def run_bot() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    bot = MusicBot(settings)
    bot.run(settings.discord_token)


def healthcheck() -> int:
    settings = load_settings()
    if shutil.which(settings.ffmpeg_path) is None:
        raise RuntimeError(f"FFmpeg executable not found: {settings.ffmpeg_path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Self-hosted Discord music bot")
    parser.add_argument("--check", action="store_true", help="validate configuration and runtime")
    args = parser.parse_args()
    if args.check:
        healthcheck()
        print("configuration and runtime checks passed")
        return
    run_bot()
