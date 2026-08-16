from __future__ import annotations

import logging
from collections.abc import Sequence

import discord
from discord import app_commands
from discord.ext import commands

from ..config import Settings
from ..music.manager import MusicManager
from ..music.models import Track
from ..music.queue import QueueFullError
from ..music.session import (
    MusicSession,
    NoPreviousTrackError,
    NothingPlayingError,
    VoiceChannelError,
)
from ..music.sources.base import SourceError

logger = logging.getLogger(__name__)


class MusicCog(commands.Cog):
    def __init__(self, manager: MusicManager, settings: Settings) -> None:
        self._manager = manager
        self._settings = settings

    @app_commands.command(name="play", description="Play a YouTube video or playlist")
    @app_commands.describe(url="A YouTube video or playlist URL")
    async def play(self, interaction: discord.Interaction, url: str) -> None:
        guild = await self._require_guild(interaction)
        voice_channel = self._user_voice_channel(interaction)
        if voice_channel is None:
            await interaction.response.send_message(
                "Join a voice channel before using `/play`.", ephemeral=True
            )
            return
        if not self._can_send_announcements(interaction):
            await interaction.response.send_message(
                "I need `View Channel` and `Send Messages` permissions in this text channel "
                "to announce tracks.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        session = self._manager.get_or_create(guild.id)
        session.set_notification_channel(interaction.channel)
        try:
            result = await self._manager.source.load(url)
            added = await session.enqueue(result.tracks, voice_channel, interaction.user.id)
        except (SourceError, QueueFullError, VoiceChannelError, ValueError) as exc:
            await interaction.followup.send(self._user_error(exc))
            return
        except Exception:
            logger.exception("Unexpected /play failure in guild %s", guild.id)
            await interaction.followup.send("Could not start playback. Check the bot logs.")
            return

        if result.is_playlist:
            description = (
                f"Queued **{added}** track(s) from **{result.source_title or 'the playlist'}**."
            )
            if result.skipped_count:
                entry_word = "entry" if result.skipped_count == 1 else "entries"
                description += f" Skipped **{result.skipped_count}** unavailable {entry_word}."
            if result.playlist_truncated:
                description += f" Limited to the first **{self._settings.max_queue_size}** entries."
        else:
            track = result.tracks[0]
            description = f"Queued [{track.title}]({track.source_url})."
        await interaction.followup.send(description)

    @app_commands.command(name="queue", description="Show the current music queue")
    async def queue(self, interaction: discord.Interaction) -> None:
        guild = await self._require_guild(interaction)
        session = self._manager.get(guild.id)
        if session is None:
            await interaction.response.send_message("The queue is empty.")
            return
        tracks = await session.queue_snapshot()
        if not tracks:
            await interaction.response.send_message("The queue is empty.")
            return
        await interaction.response.send_message(self._format_queue(tracks))

    @app_commands.command(name="nowplaying", description="Show the currently playing track")
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        guild = await self._require_guild(interaction)
        session = self._manager.get(guild.id)
        if session is None:
            await interaction.response.send_message("Nothing is currently playing.")
            return
        track, state = await session.current()
        if track is None:
            await interaction.response.send_message("Nothing is currently playing.")
            return
        await interaction.response.send_message(
            f"[{track.title}]({track.source_url}) ({track.duration_text})\nState: `{state}`"
        )

    @app_commands.command(name="skip", description="Skip the current track")
    async def skip(self, interaction: discord.Interaction) -> None:
        await self._skip_command(interaction)

    @app_commands.command(name="next", description="Skip to the next track")
    async def next(self, interaction: discord.Interaction) -> None:
        await self._skip_command(interaction)

    @app_commands.command(name="previous", description="Replay the previous track")
    async def previous(self, interaction: discord.Interaction) -> None:
        await self._previous_command(interaction)

    @app_commands.command(name="back", description="Replay the previous track")
    async def back(self, interaction: discord.Interaction) -> None:
        await self._previous_command(interaction)

    @app_commands.command(name="clear", description="Stop playback and clear the queue")
    async def clear(self, interaction: discord.Interaction) -> None:
        session = await self._session_for_voice_command(interaction)
        if session is None:
            return
        await session.clear_queue()
        await interaction.response.send_message("Playback stopped and the queue was cleared.")

    async def _skip_command(self, interaction: discord.Interaction) -> None:
        session = await self._session_for_voice_command(interaction)
        if session is None:
            return
        try:
            await session.skip()
        except NothingPlayingError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message("Skipped the current track.")

    async def _previous_command(self, interaction: discord.Interaction) -> None:
        session = await self._session_for_voice_command(interaction)
        if session is None:
            return
        try:
            await session.previous()
        except NoPreviousTrackError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        except QueueFullError as exc:
            await interaction.response.send_message(
                f"The queue is full, so the previous track could not be restored. {exc}",
                ephemeral=True,
            )
            return
        await interaction.response.send_message("Replaying the previous track.")

    @app_commands.command(name="stop", description="Stop playback and clear the queue")
    async def stop(self, interaction: discord.Interaction) -> None:
        session = await self._session_for_voice_command(interaction)
        if session is None:
            return
        await session.stop()
        await interaction.response.send_message("Playback stopped and the queue was cleared.")

    @app_commands.command(name="leave", description="Disconnect the bot from voice")
    async def leave(self, interaction: discord.Interaction) -> None:
        guild = await self._require_guild(interaction)
        session = self._manager.get(guild.id)
        if session is None or not await self._require_same_voice(interaction, session):
            return
        await session.leave()
        await interaction.response.send_message("Disconnected from voice and cleared the queue.")

    async def _session_for_voice_command(
        self, interaction: discord.Interaction
    ) -> MusicSession | None:
        guild = await self._require_guild(interaction)
        session = self._manager.get(guild.id)
        if session is None:
            await interaction.response.send_message(
                "The bot is not in a voice channel.", ephemeral=True
            )
            return None
        if not await self._require_same_voice(interaction, session):
            return None
        return session

    async def _require_guild(self, interaction: discord.Interaction) -> discord.Guild:
        if interaction.guild is None:
            raise ValueError("This command can only be used in a server.")
        return interaction.guild

    async def _require_same_voice(
        self, interaction: discord.Interaction, session: MusicSession
    ) -> bool:
        channel = self._user_voice_channel(interaction)
        if channel is None:
            await interaction.response.send_message(
                "Join the bot's voice channel first.", ephemeral=True
            )
            return False
        if not await session.is_in_channel(channel.id):
            await interaction.response.send_message(
                "You must be in the same voice channel as the bot.", ephemeral=True
            )
            return False
        return True

    @staticmethod
    def _user_voice_channel(
        interaction: discord.Interaction,
    ) -> discord.VoiceChannel | discord.StageChannel | None:
        user = interaction.user
        voice_state = getattr(user, "voice", None)
        channel = getattr(voice_state, "channel", None)
        return (
            channel if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)) else None
        )

    @staticmethod
    def _can_send_announcements(interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        channel = interaction.channel
        member = guild.me if guild is not None else None
        permissions_for = getattr(channel, "permissions_for", None)
        if member is None or not callable(permissions_for):
            return True
        permissions = permissions_for(member)
        return permissions.view_channel and permissions.send_messages

    @staticmethod
    def _format_queue(tracks: Sequence[Track]) -> str:
        lines = ["**Queue**"]
        for index, track in enumerate(tracks[:20], start=1):
            lines.append(f"`{index}.` [{track.title}]({track.source_url}) ({track.duration_text})")
        if len(tracks) > 20:
            lines.append(f"...and {len(tracks) - 20} more.")
        return "\n".join(lines)

    @staticmethod
    def _user_error(error: Exception) -> str:
        if isinstance(error, VoiceChannelError):
            return str(error)
        if isinstance(error, QueueFullError):
            return f"The queue is full. {error}"
        if isinstance(error, SourceError):
            return str(error)
        return "That request could not be added to the queue."
