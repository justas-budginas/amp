# Self-Hosted Discord Music Bot

## Goal

Build a self-hosted Discord music bot that joins the voice channel of the user who invokes a command and plays audio from a requested source. YouTube will be the first supported source, while the application architecture should allow additional sources later.

Docker is the primary way to run the bot on a server. Local Python execution is mainly for development and troubleshooting.

`README.md` is the primary user-facing documentation for installing, configuring, running, updating, and troubleshooting the bot.

## Implementation Status

### Implemented

- Docker-first deployment with a non-root Node.js 22 and Python runtime, FFmpeg, health checks, and Docker Compose.
- Slash-command playback for YouTube videos and playlists.
- Ordered playlist queueing, bounded by `MAX_QUEUE_SIZE`.
- Per-guild in-memory queues and playback sessions.
- `/play`, `/queue`, `/nowplaying`, `/skip`, `/next`, `/previous`, `/back`, `/clear`, `/stop`, and `/leave`.
- Fresh YouTube stream resolution before playback, media relayed through yt-dlp's HTTP client to FFmpeg stdin, and audio-only format selection.
- Silent clickable now-playing announcements when the bot can send messages in the command channel.
- Automated unit tests, Ruff checks, and Mypy checks.

### Not Implemented Yet

- Full-song prebuffering, temporary-file downloads, or Lavalink-based playback. The current player streams directly from YouTube and can still experience network-related jitter.
- Pause and resume commands.
- Per-user queue limits and command cooldown enforcement. `COMMAND_COOLDOWN_SECONDS` is currently reserved configuration.
- Automatic retry after a mid-playback FFmpeg or network failure. Tracks are re-resolved before starting playback.
- Persistent queues or playback state.
- YouTube search, additional media sources, and playlist controls such as remove or shuffle.
- Dedicated integration tests against Discord, YouTube, and a built Docker image.

## Recommended Stack

- Python 3.11 or newer
- `discord.py` 2.x with voice support
- Slash commands through Discord application commands
- `yt-dlp` through its Python API for YouTube extraction
- FFmpeg for audio processing and streaming
- `pytest` and `pytest-asyncio` for tests
- Ruff for formatting and linting
- Mypy or an equivalent type checker
- Docker with a small Linux runtime image
- Docker Compose for convenient self-hosted deployment

The Docker image must include FFmpeg. YouTube extraction dependencies, including any JavaScript runtime required by the current `yt-dlp` release, must be installed and versioned as part of the image instead of being installed manually on the host.

## MVP Scope

### Commands

- `/play <youtube_url_or_playlist>`: Validate the URL, join the invoking user's voice channel, enqueue a video or expand a playlist into ordered tracks, and start playback when idle.
- `/queue`: Show the current server queue.
- `/nowplaying`: Show the current track and playback state.
- `/skip`: Stop the current track and start the next one.
- `/next`: Alias for `/skip`.
- `/previous`: Replay the previous track.
- `/back`: Alias for `/previous`.
- `/clear`: Stop playback and clear the queue.
- `/stop`: Stop playback and clear the queue.
- `/leave`: Disconnect from voice and clear the server session.

### MVP Behavior

- Support direct YouTube video URLs and YouTube playlist URLs.
- When a playlist is provided, preserve the source order and add its tracks to the guild queue before playback advances through them one at a time.
- Apply a configurable maximum playlist size to prevent accidental or abusive queue growth; if exceeded, queue only the first configured number of entries instead of rejecting the playlist.
- Report playlist expansion results, including the number of queued, skipped, and unavailable entries.
- Maintain one independent music session per Discord server.
- Require the invoking user to be connected to a voice channel.
- Join the user's current voice channel when no session exists.
- Reject or clearly report attempts to control a session from another voice channel.
- Disconnect automatically after a configurable idle timeout.
- Keep queues in memory initially; document that a restart loses active queues.
- Do not permanently download or store media files.
- Send a silent, clickable now-playing message whenever a track starts.

## Architecture

Use clear boundaries so Discord-specific behavior, queue state, media extraction, and playback can evolve independently.

```text
discord-music-bot/
├── src/
│   └── discord_music_bot/
│       ├── __init__.py
│       ├── __main__.py
│       ├── config.py
│       ├── bot.py
│       ├── commands/
│       │   └── music.py
│       └── music/
│           ├── models.py
│           ├── queue.py
│           ├── manager.py
│           ├── session.py
│           ├── player.py
│           └── sources/
│               ├── base.py
│               └── youtube.py
├── tests/
│   └── unit/
├── Dockerfile
├── compose.yaml
├── .dockerignore
├── .env.amp.example
├── pyproject.toml
├── README.md
└── docs/
    └── project.md
```

### Application Components

- `config.py`: Load and validate environment-based settings.
- `bot.py`: Configure intents, register commands, configure logging, and perform startup/shutdown handling.
- `commands/music.py`: Handle slash commands, validate Discord context, and delegate to the music manager. Command handlers should not own playback logic.
- `music/models.py`: Define track metadata, playback state, and source-independent data structures.
- `music/queue.py`: Implement bounded FIFO queue operations.
- `music/manager.py`: Own one music session per guild and provide guild-level isolation.
- `music/session.py`: Manage voice connection, queue, current track, playback worker, locks, idle timeout, and cleanup.
- `music/player.py`: Create FFmpeg audio sources, handle playback callbacks, and translate process failures into application errors.
- `music/sources/base.py`: Define the source adapter interface so future sources do not affect command or session code.
- `music/sources/youtube.py`: Validate YouTube URLs and adapt `yt-dlp` metadata into the common track model.

## Playback Flow

1. A user invokes `/play` with a YouTube URL.
2. The command verifies that the user is in a voice channel and that the bot has `View Channel`, `Connect`, and `Speak` permissions.
3. The command asks the guild music session to accept the request.
4. The YouTube source adapter validates the hostname and extracts metadata without downloading the complete media file.
5. Extraction runs outside the Discord event loop because the `yt-dlp` API is blocking.
6. For a playlist, the source adapter expands entries in their original order and returns normalized tracks or entry-level failures.
7. The session validates the complete expansion against queue and playlist limits, then adds the accepted tracks atomically so a partially expanded request does not leave an unexpected partial queue.
8. A session playback worker starts the next track if nothing is currently playing.
9. yt-dlp opens the resolved media stream and relays it to FFmpeg stdin; FFmpeg supplies Discord-compatible audio without making a second HTTP request to YouTube.
10. The playback completion callback schedules the next queue operation safely on the asyncio event loop.
11. When the queue is empty, the session waits for the idle timeout and disconnects.

Stream URLs can expire. The current implementation extracts immediately before playback. Automatic retry after a mid-playback FFmpeg failure is not implemented yet.

## Concurrency and Reliability

- Use one `asyncio.Lock` per guild session for queue and playback state changes.
- Ensure only one playback worker can advance a session at a time.
- Handle cancellation and shutdown by stopping FFmpeg, cancelling worker tasks, disconnecting voice, and releasing resources.
- Convert voice connection failures, missing permissions, extraction failures, and FFmpeg failures into concise user-facing messages.
- Add timeouts around metadata extraction and voice operations.
- Avoid blocking calls such as synchronous extraction or subprocess waiting on the Discord event loop.
- Handle Discord reconnects and voice disconnects without leaving stale session state.
- Prevent unbounded queues with the configurable per-guild `MAX_QUEUE_SIZE` limit. Per-user queue limits are not implemented yet.
- Do not hold the session lock while expanding a playlist; perform extraction first, then atomically commit the ordered tracks to the queue.
- Keep playlist expansion bounded by entry count, URL length, extraction timeout, and total queue capacity.
- Continue after unavailable playlist entries where possible and report skipped entries without stopping valid tracks from playing.

## Observability and Diagnostics

- Log important transitions at component boundaries, including queue dequeue, YouTube resolution, FFmpeg creation, voice playback start, first audio frame, stream completion, callback errors, and cleanup.
- Include enough context to correlate a failure without logging secrets: guild ID, track title, queue position or remaining size, stream host and format, FFmpeg PID, frame count, elapsed time, and process return code where available.
- Continue adding targeted logging wherever a new asynchronous, network, voice, or subprocess boundary makes failures difficult to localize. Add or update tests when diagnostics expose sensitive data or rely on a specific lifecycle event.
- Use `INFO` for normal lifecycle milestones, `WARNING` for failed or incomplete playback, and `DEBUG` for high-volume diagnostic details.
- Never log bot tokens, cookies, signed stream URLs, authorization headers, environment values, or raw exception text that may contain them. Log only allowlisted metadata such as IDs, hostnames, format identifiers, counts, return codes, and exception class names.
- Apply centralized redaction as defense in depth, and cover token, header, signed-URL query, and exception-message redaction with automated tests.
- Do not run or capture diagnostic commands that expand secret-bearing configuration, including `docker compose config`, `env`, `printenv`, or equivalent commands, unless their output is filtered before it reaches logs, tool output, CI artifacts, or chat.

## Docker-First Deployment

### Docker Image

Create a production Docker image that:

- Uses a maintained slim Node.js 22 base image with Python 3 and FFmpeg installed.
- Installs Python dependencies from `pyproject.toml`; a fully pinned lockfile is not implemented yet.
- Installs FFmpeg and all runtime libraries needed by Discord voice playback.
- Installs the JavaScript runtime and `yt-dlp` support components required by the selected YouTube extraction configuration.
- Copies only application source and required metadata into the final image.
- Runs as a non-root user.
- Uses an unbuffered process and forwards logs to standard output.
- Defines a health check or startup diagnostic that confirms configuration and required executables are available.
- Does not include secrets, local `.env.amp` files, cookies, caches, test files, or development tooling in the production layer.

Use a multi-stage build if it reduces the final image size without making maintenance harder.

### Docker Compose

Provide `compose.yaml` with:

- The bot service.
- Environment variable loading from a local `.env.amp` file.
- Automatic restart policy suitable for a self-hosted server.
- No unnecessary exposed ports because the bot primarily uses Discord's outbound connections.
- A read-only application filesystem where practical.
- A small writable temporary directory only if a dependency requires one.
- Resource limits or documented recommendations for CPU and memory.
- Log configuration suitable for long-running operation.
- Pull the published image from GitHub Container Registry instead of building on the server.

The bot token must never be stored in the image or committed to the repository.

### Server Installation Documentation

Document the following workflow in `README.md`:

1. Create a Discord application and bot.
2. Copy the bot token into a server-side `.env.amp` file.
3. Invite the bot with the `bot` and `applications.commands` scopes.
4. Grant only the required permissions: `View Channel`, `Send Messages`, `Embed Links`, `Connect`, and `Speak`.
5. Install Docker and Docker Compose on the server.
6. Pull and start the published image with `docker compose pull` followed by `docker compose up -d`.
7. Inspect logs with `docker compose logs -f`.
8. Stop or update the service using Docker Compose.

## Configuration

Start with environment variables:

- `DISCORD_TOKEN`: Required bot token.
- `DISCORD_APPLICATION_ID`: Optional application ID for command registration.
- `DISCORD_TEST_GUILD_ID`: Optional development guild ID for fast command synchronization.
- `LOG_LEVEL`: Logging level, defaulting to `INFO`.
- `IDLE_DISCONNECT_SECONDS`: Idle voice disconnect timeout.
- `MAX_QUEUE_SIZE`: Maximum tracks per guild queue and maximum entries accepted from a single playlist request.
- `EXTRACTION_TIMEOUT_SECONDS`: Maximum time allowed for metadata extraction.
- `COMMAND_COOLDOWN_SECONDS`: Reserved configuration for future command cooldown enforcement.
- `FFMPEG_PATH`: Optional override for the FFmpeg executable path.

Use `.env.amp.example` for local development. Use Docker secrets or the host environment for production deployments when available.

The GitHub Actions workflow publishes `ghcr.io/justas-budginas/amp` for pushes to `develop` and `main`, version tags, and manual runs. The server Compose configuration follows the `develop` tag.

## YouTube Source Design

- Allow only approved YouTube hostnames for the first release: `youtube.com`, `www.youtube.com`, `music.youtube.com`, and `youtu.be`.
- Reject arbitrary extractor URLs until additional sources are intentionally supported.
- Use the `yt-dlp` Python API rather than constructing shell commands from user input.
- Detect playlist URLs and expand them with playlist extraction enabled.
- Preserve playlist entry order when converting entries into queue items.
- Prefer flat playlist metadata during expansion and extract each playable stream immediately before playback when practical, because direct stream URLs expire.
- Use `MAX_QUEUE_SIZE` for both the per-guild queue limit and the maximum number of playlist entries fetched from one request.
- Treat unavailable, private, deleted, or region-restricted entries as per-entry failures; continue with the remaining entries and summarize the result to the requesting channel.
- Normalize title, URL, duration, thumbnail, uploader, and source information into a common track model.
- Avoid browser cookies by default. If authenticated extraction is later required, treat cookies as sensitive secrets and document the risks.
- Pin or constrain `yt-dlp` versions and provide a documented update process because upstream site changes can break extraction.
- The image installs `yt-dlp[default]`, `yt-dlp-ejs`, and Node.js 22; the adapter explicitly enables Node for YouTube extraction and prefers an audio-only stream for FFmpeg playback.

## Testing Strategy

### Unit Tests

- YouTube URL validation, playlist detection, expansion, ordering, size limits, and per-entry failure handling.
- Track model normalization.
- Queue ordering, limits, removal, and clearing.
- Guild session isolation.
- State transitions for idle, playing, paused, stopped, and disconnected sessions.
- Concurrent `/play`, `/skip`, `/stop`, and `/leave` operations.
- Permission and voice-channel validation.
- Extraction timeout and extraction failure handling.
- Fresh stream resolution before playback. Mid-playback stream retry is not implemented yet.
- FFmpeg failure and cleanup behavior.

### Integration Tests

- Use mocked Discord voice clients and mocked `yt-dlp` responses.
- Verify that command handlers delegate correctly without requiring a live Discord connection.
- Build the Docker image in CI and verify that Python, FFmpeg, and the configured extraction runtime are available.
- Keep live YouTube and Discord smoke tests manual or opt-in so CI does not depend on external services.

### Quality Checks

Run formatting, linting, type checking, and tests locally and in CI before merging changes.

## Implementation Phases

### Phase 0: Foundation

- Add `pyproject.toml` and the `src` package layout.
- Add configuration loading and structured logging.
- Add development dependencies and test configuration.
- Add `.env.amp.example` and update `.gitignore` for runtime artifacts.
- Create the initial Dockerfile, `.dockerignore`, and Compose file.
- Verify that the container starts and fails clearly when `DISCORD_TOKEN` is missing.

### Phase 1: Discord Connectivity

- Create the Discord application and document setup.
- Implement bot startup and graceful shutdown.
- Register a basic `/ping` or diagnostic command.
- Configure only the required gateway intents; message content intent should not be needed for slash commands.
- Verify the container can connect to Discord.

### Phase 2: YouTube MVP Playback

- Implement the source adapter and normalized track model.
- Implement playlist expansion results and ordered track batches.
- Implement queue and per-guild session management.
- Implement `/play`, `/queue`, `/nowplaying`, `/skip`, `/next`, `/previous`, `/back`, `/clear`, `/stop`, and `/leave`.
- Queue playlist entries in source order and play them sequentially.
- Add FFmpeg playback and completion handling.
- Add voice permission checks, extraction timeouts, queue and playlist limits, and idle disconnect.
- Verify playback manually in a disposable Discord test server.

### Phase 3: Reliability

- Add pause and resume.
- Add retry and re-extraction after mid-playback stream failures.
- Improve voice reconnect handling.
- Add command cooldowns and clearer error responses.
- Add shutdown cleanup and operational diagnostics.

### Phase 4: Production Readiness

- Harden the Docker image and run as non-root.
- Add CI for tests, static checks, and image builds.
- Add image versioning and dependency update documentation.
- Document backups, logs, restart behavior, resource expectations, and troubleshooting.

### Phase 5: Optional Extensions

- YouTube search using a controlled query format.
- Additional source adapters.
- Persistent configuration or queues if there is a demonstrated need.
- Per-guild permissions and administrator-only controls.
- Metrics or a lightweight health endpoint if operational monitoring requires it.

## Security and Policy Requirements

- Never commit bot tokens, `.env.amp` files, browser cookies, or extracted credentials.
- Never interpolate user-provided URLs into shell command strings.
- Use argument arrays and controlled FFmpeg options.
- Enforce URL length, queue size, extraction timeout, and command rate limits.
- Keep Discord permissions minimal.
- Redact tokens, cookies, direct stream URLs, and sensitive configuration from logs.
- Treat terminal, tool, CI, and chat output as external disclosure surfaces even when the service itself runs on an owned machine. Never emit resolved environment or secret values into those surfaces.
- Document that operators are responsible for complying with Discord rules, YouTube terms, copyright law, and applicable local regulations.

## Definition of Done for the First Release

- The bot runs from the production Docker image on a Linux server.
- A user can invoke `/play` with a supported YouTube URL and hear audio in their current voice channel.
- A user can invoke `/play` with a supported YouTube playlist URL and have its playable entries queued in source order.
- Playlist entries play one after another without requiring another command between tracks.
- Oversized playlists and unavailable entries are handled without unbounded memory or queue growth.
- Multiple guilds can use the same bot without sharing queues or playback state.
- The queue and playback commands work reliably under normal concurrent use.
- The bot reports missing permissions, invalid URLs, unavailable videos, and playback failures clearly.
- The bot disconnects after being idle and cleans up after shutdown.
- No media files or secrets are persisted by default.
- Automated tests and Docker image validation pass.
- `README.md` contains complete setup, deployment, update, and troubleshooting instructions.

## Initial Technical Decisions

- Python is preferred because the repository already uses Python-oriented ignore rules and the Discord/YouTube/FFmpeg integration is well supported.
- Slash commands are preferred over message-prefix commands because they avoid requiring the privileged Message Content intent.
- Docker is the canonical production runtime.
- Playback state is in memory for the MVP.
- YouTube direct video and playlist URLs are supported before search and other sources.
- Source adapters are introduced from the beginning to avoid coupling the core player to YouTube.
