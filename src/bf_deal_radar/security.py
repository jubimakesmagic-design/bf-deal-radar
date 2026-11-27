"""Security utilities: secret redaction for logs and strict URL validation."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from urllib.parse import urlparse

_SECRET_PATTERNS: list[re.Pattern[str]] = [
    # Telegram bot token in URL or raw format (e.g. bot123456789:AAH... or 123456789:AAH...)
    re.compile(r"(?:bot)?\d{6,12}:[A-Za-z0-9_-]{20,}"),
    # OpenAI / Anthropic style API keys
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    # Google / Gemini API keys
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    # GitHub personal / OAuth / fine-grained tokens
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    # Authorization Bearer header values
    re.compile(r"(Bearer\s+)[^\s\"']+", re.IGNORECASE),
]

_RUNTIME_SECRETS: set[str] = set()


def register_secrets(*values: str) -> None:
    """Register runtime secret strings so they are scrubbed from all logs."""
    for val in values:
        cleaned = (val or "").strip()
        if len(cleaned) >= 4:
            _RUNTIME_SECRETS.add(cleaned)


def clear_registered_secrets() -> None:
    """Clear registered runtime secrets (primarily for unit tests)."""
    _RUNTIME_SECRETS.clear()


def redact_secrets(text: str, extra_secrets: Iterable[str] = ()) -> str:
    """Scrub registered secrets and well-known token/API-key patterns from a string."""
    if not text:
        return text

    result = text
    secrets_to_scrub = list(_RUNTIME_SECRETS) + [s.strip() for s in extra_secrets if s and s.strip()]
    # Replace longest secrets first to prevent partial overlaps
    for secret in sorted(set(secrets_to_scrub), key=len, reverse=True):
        if len(secret) >= 4 and secret in result:
            result = result.replace(secret, "[REDACTED]")

    for pattern in _SECRET_PATTERNS:
        if pattern.groups == 1:
            result = pattern.sub(r"\1[REDACTED]", result)
        else:
            result = pattern.sub("[REDACTED]", result)

    return result


class SecretRedactingFilter(logging.Filter):
    """Logging filter that guarantees secrets never appear in formatted log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:
            rendered = str(record.msg)
        record.msg = redact_secrets(rendered)
        record.args = ()
        if record.exc_text:
            record.exc_text = redact_secrets(record.exc_text)
        return True


def is_safe_http_url(
    url: str,
    *,
    require_https: bool = False,
    allow_localhost: bool = False,
) -> bool:
    """Validate that a URL has a safe http(s) scheme and non-empty host."""
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if not host:
        return False

    if require_https:
        if scheme == "https":
            return True
        if allow_localhost and scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}:
            return True
        return False

    return scheme in {"http", "https"}
