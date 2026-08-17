# AMP - Self-Hosted Discord Music Bot

AMP is a Docker-first Discord music bot that joins a user's voice channel and plays YouTube videos and playlists in order.

`README.md` is the primary user-facing documentation for installing, configuring, running, updating, and troubleshooting the bot.

## Requirements

- A Discord application and bot token
- Docker Engine and Docker Compose
- A Discord server where you can invite the bot

The production container includes Python, FFmpeg, Node.js 22, and the YouTube extraction dependencies. FFmpeg and `yt-dlp` do not need to be installed on the host.

## Discord Setup

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and create an application.
2. Open the **Bot** page and create a bot user.
3. Copy the bot token. Never commit it to Git.
4. Open **OAuth2 > URL Generator**.
5. Select the `bot` and `applications.commands` scopes.
6. Grant these bot permissions:
   - View Channel
   - Send Messages
   - Embed Links
   - Connect
   - Speak
7. Invite the generated bot URL to the target server.

The bot uses slash commands and does not require the privileged Message Content intent.

For automatic now-playing announcements, the bot also needs `View Channel` and `Send Messages` in the text channel where `/play` is used. Category or channel permission overrides must not deny those permissions.

## Docker Deployment

1. Clone the repository on the server.
2. Create the environment file:

   ```sh
   cp .env.amp.example .env.amp
   ```

3. Put the bot token in `.env.amp`:

   ```text
   DISCORD_TOKEN=your-token
   ```

4. Pull and start the published container:

   ```sh
   docker compose pull
   docker compose up -d
   ```

   The image is published by GitHub Actions as `ghcr.io/justas-budginas/amp:develop`.
   If the GitHub Container Registry package is private, authenticate first:

   ```sh
   read -s GHCR_TOKEN
   echo "$GHCR_TOKEN" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
   ```

5. Follow the logs:

   ```sh
   docker compose logs -f bot
   ```

6. Update the bot:

   ```sh
   docker compose pull
   docker compose up -d
   ```

7. Stop the bot:

   ```sh
   docker compose down
   ```

The bot does not expose an HTTP port. It connects outbound to Discord and YouTube.

## Commands

- `/play <url>`: Play a YouTube video or queue a YouTube playlist.
- `/queue`: Show queued tracks.
- `/nowplaying`: Show the current track.
- `/skip`: Skip the current track.
- `/next`: Alias for `/skip`.
- `/previous`: Replay the previous track.
- `/back`: Alias for `/previous`.
- `/clear`: Stop playback and clear the queue.
- `/stop`: Stop playback and clear the queue.
- `/leave`: Disconnect and clear the queue.

Playlist entries are queued in their original order and play one after another. Unavailable entries are skipped and reported. If a playlist exceeds `MAX_QUEUE_SIZE`, only its first configured number of entries are queued and the command reports that it was limited.

The bot sends a silent, clickable `Now playing` message whenever a track starts. The link opens the original YouTube video.

## Configuration

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | Yes | | Discord bot token |
| `DISCORD_APPLICATION_ID` | No | | Discord application ID |
| `DISCORD_TEST_GUILD_ID` | No | | Guild ID for immediate command synchronization during development |
| `LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |
| `IDLE_DISCONNECT_SECONDS` | No | `300` | Time before disconnecting while idle |
| `MAX_QUEUE_SIZE` | No | `100` | Maximum queued tracks per server and maximum entries accepted from one playlist |
| `EXTRACTION_TIMEOUT_SECONDS` | No | `45` | YouTube metadata and stream extraction timeout |
| `COMMAND_COOLDOWN_SECONDS` | No | `3` | Reserved command cooldown setting |
| `FFMPEG_PATH` | No | `ffmpeg` | FFmpeg executable path |

Queues are held in memory. Restarting the container clears active queues and playback state.

Playback relays YouTube media through yt-dlp's HTTP client to FFmpeg stdin rather than prebuffering complete tracks. Short network or CDN interruptions may still cause audio jitter; Lavalink or temporary-file playback is not implemented yet.

## Playback Troubleshooting

Follow the playback lifecycle for a failing track with:

```sh
docker compose logs --since 10m bot
```

Useful diagnostic messages include:

- `FFmpeg produced first audio frame`: FFmpeg opened the stream and returned audio.
- `YouTube media stream opening failed ... status=403`: YouTube rejected the resolved stream before audio started.
- `FFmpeg stream ended ... frames=0`: FFmpeg could not decode audio from the relayed stream.
- `FFmpeg stream ended ... frames>0`: The stream failed after playback had begun.
- `FFmpeg cleanup ... returncode=None->-9`: FFmpeg was forcefully cleaned up while still running; correlate this with the preceding stream and callback messages before treating it as the root cause.

When reporting a playback failure, include the messages from `Dequeued track` through `Playback state reset` for that track. Do not paste signed YouTube stream URLs, tokens, cookies, or authorization headers from the logs.

## Local Development

Python 3.11 or newer is required.

```sh
python -m venv .venv
python -m pip install --editable ".[dev]"
copy .env.amp.example .env.amp
python -m discord_music_bot --check
python -m discord_music_bot
```

On Linux and macOS, use `cp .env.amp.example .env.amp` instead of `copy`.

For local execution, install FFmpeg and Node.js 22 or another supported JavaScript runtime separately. Docker is the recommended production runtime.

## Verification

```sh
python -m ruff check .
python -m mypy src
python -m pytest
docker build -t amp:local .
```

Live Discord and YouTube smoke tests should use a disposable test server and a permitted test URL. They are not required for the automated test suite.

## Security and Policy

- Keep `.env.amp` outside source control.
- Do not add browser cookies to the image or repository.
- Do not publish bot tokens or extracted stream URLs in logs.
- Operators are responsible for complying with Discord rules, YouTube terms, copyright law, and applicable regulations.
