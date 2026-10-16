"""Structured JSON logging with mandatory redaction (CE-15)."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from chat_engine.observability.redaction import redact, redact_text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_text(record.getMessage()),
        }
        extra = getattr(record, "ce", None)
        if isinstance(extra, dict):
            payload.update(redact(extra))
        if record.exc_info:
            payload["exc"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> logging.Logger:
    root = logging.getLogger("chat_engine")
    if not any(
        isinstance(h, logging.StreamHandler) and isinstance(h.formatter, JsonFormatter)
        for h in root.handlers
    ):
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(JsonFormatter())
        root.addHandler(h)
    root.setLevel(level.upper())
    root.propagate = False
    return root


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    logger.info(event, extra={"ce": {"event": event, **fields}})


__all__ = ["JsonFormatter", "configure_logging", "log_event"]
