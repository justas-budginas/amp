FROM node:22-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

ENV PATH="/opt/venv/bin:$PATH"

USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD ["python", "-m", "discord_music_bot", "--check"]

CMD ["python", "-m", "discord_music_bot"]
