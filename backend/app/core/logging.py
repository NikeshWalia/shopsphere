"""Structured application logging.

Two formats are supported: ``json`` (default; one object per line, suited to log
shippers and to CI artifacts) and ``console`` (human readable, used locally).

Redaction is applied centrally rather than trusted to call sites: any key whose
name looks like a credential is replaced before the record is emitted, so an
accidental ``logger.info("...", extra={"password": ...})`` cannot leak.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings

# Correlation identifiers for the request currently being served. ContextVars
# are the right tool here: they are isolated per asyncio task, so concurrent
# requests never observe each other's values.
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_ctx: ContextVar[int | None] = ContextVar("user_id", default=None)

SENSITIVE_KEYS = frozenset(
    {
        "password",
        "current_password",
        "new_password",
        "password_confirm",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "secret",
        "secret_key",
        "api_key",
        "card_number",
        "cvv",
        "cvc",
        "pan",
    }
)
REDACTED = "***redacted***"

# Attributes present on every LogRecord; anything else was supplied by the
# caller via ``extra=`` and therefore belongs in the structured payload.
_STANDARD_RECORD_KEYS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
) | {"message", "asctime", "taskName"}


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively replace credential-shaped values with a placeholder."""
    if _depth > 6:
        return value
    if isinstance(value, dict):
        return {
            key: (REDACTED if str(key).lower() in SENSITIVE_KEYS else redact(val, _depth + 1))
            for key, val in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item, _depth + 1) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if (request_id := request_id_ctx.get()) is not None:
            payload["request_id"] = request_id
        if (user_id := user_id_ctx.get()) is not None:
            payload["user_id"] = user_id

        extras = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_RECORD_KEYS}
        if extras:
            payload.update(redact(extras))

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, separators=(",", ":"))


class ConsoleFormatter(logging.Formatter):
    """Compact, aligned output for local development."""

    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created, tz=UTC).strftime("%H:%M:%S.%f")[:-3]
        request_id = request_id_ctx.get()
        prefix = f"[{request_id[:8]}] " if request_id else ""
        extras = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_RECORD_KEYS}
        suffix = ""
        if extras:
            rendered = " ".join(f"{k}={v}" for k, v in redact(extras).items())
            suffix = f"  {rendered}"
        line = (
            f"{stamp} {record.levelname:<7} {record.name:<28} {prefix}{record.getMessage()}{suffix}"
        )
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging() -> None:
    """Install the configured formatter on the root logger.

    Idempotent: repeated calls (uvicorn reloads, test sessions) replace the
    handler rather than stacking duplicates.
    """
    formatter: logging.Formatter = (
        JsonFormatter() if settings.log_format == "json" else ConsoleFormatter()
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    # uvicorn installs its own colourised handlers; route them through ours so
    # that every line in the process has the same shape.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    # uvicorn.access duplicates our own access log middleware.
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.db_echo else logging.WARNING
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_business_event(event: str, **fields: Any) -> None:
    """Emit a named business event.

    Business events (order placed, payment declined, stock exhausted) are the
    log lines worth alerting on, so they are tagged with ``event_type`` to make
    them trivially greppable and separable from request noise.
    """
    logging.getLogger("shopsphere.events").info(event, extra={"event_type": event, **fields})
