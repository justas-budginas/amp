from __future__ import annotations

import logging
import re
from collections.abc import Iterable

_AUTHORIZATION = re.compile(
    r"(?i)\b(?:authorization|proxy-authorization)\s*:\s*\S+(?:\s+\S+)?"
)
_COOKIE = re.compile(r"(?i)\b(?:cookie|set-cookie)\s*:\s*\S+")
_DISCORD_TOKEN = re.compile(r"\b(?:mfa\.[\w-]{20,}|[\w-]{20,}\.[\w-]{6}\.[\w-]{20,})\b")
_URL_QUERY = re.compile(r"(https?://[^\s?]+)\?\S+")


def redact_sensitive_data(value: str, secrets: Iterable[str] = ()) -> str:
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "<redacted>")
    redacted = _AUTHORIZATION.sub("Authorization: <redacted>", redacted)
    redacted = _COOKIE.sub("Cookie: <redacted>", redacted)
    redacted = _DISCORD_TOKEN.sub("<redacted>", redacted)
    return _URL_QUERY.sub(r"\1?<redacted>", redacted)


class RedactingFormatter(logging.Formatter):
    def __init__(self, fmt: str, *, secrets: Iterable[str] = ()) -> None:
        super().__init__(fmt)
        self._secrets = tuple(secrets)

    def format(self, record: logging.LogRecord) -> str:
        return redact_sensitive_data(super().format(record), self._secrets)
