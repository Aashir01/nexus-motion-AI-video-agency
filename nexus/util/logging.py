"""Structured logging with request/job correlation."""
from __future__ import annotations

import json
import logging
import sys
import time
from contextvars import ContextVar
from typing import Any

from nexus.config import settings

# A mutable default would be shared by every context; None means "empty".
_context: ContextVar[dict[str, Any] | None] = ContextVar("log_context", default=None)

_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(_context.get() or {})
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[36m", "INFO": "\033[32m", "WARNING": "\033[33m",
        "ERROR": "\033[31m", "CRITICAL": "\033[35m",
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        ctx = _context.get() or {}
        suffix = " ".join(f"{k}={v}" for k, v in ctx.items())
        extras = " ".join(
            f"{k}={v}" for k, v in record.__dict__.items()
            if k not in _RESERVED and not k.startswith("_")
        )
        tail = " ".join(x for x in (suffix, extras) if x)
        base = (
            f"{color}{record.levelname:<8}\033[0m "
            f"\033[90m{record.name}\033[0m {record.getMessage()}"
        )
        if tail:
            base += f" \033[90m{tail}\033[0m"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter() if settings.log_format == "json" else ConsoleFormatter()
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level.upper())
    for noisy in ("httpx", "httpcore", "botocore", "aiobotocore", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def bind(**kwargs: Any):
    """Bind correlation fields for the current async context."""
    merged = {**(_context.get() or {}), **{k: v for k, v in kwargs.items() if v is not None}}
    return _context.set(merged)


def unbind(token) -> None:
    _context.reset(token)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
