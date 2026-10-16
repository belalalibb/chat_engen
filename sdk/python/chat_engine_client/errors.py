from __future__ import annotations

from typing import Any


class ChatEngineAPIError(Exception):
    """Any non-2xx reply. ``code`` is the chat_engine / upstream / OpenAI-compat code."""

    def __init__(self, status: int, body: Any) -> None:
        err = body.get("error", {}) if isinstance(body, dict) else {}
        self.status = status
        self.code: str = str(err.get("code") or "http_error")
        self.message: str = str(err.get("message") or body)
        self.details: dict[str, Any] = dict(err.get("details") or {})
        self.retryable: bool = bool(err.get("retryable", status in (429, 503)))
        self.body = body
        super().__init__(f"{status} {self.code}: {self.message}")

    @property
    def retry_after_s(self) -> float | None:
        ms = self.details.get("retry_after_ms")
        return float(ms) / 1000 if isinstance(ms, (int, float)) else None


class RetryExhausted(ChatEngineAPIError):
    """Raised after the configured number of retries on retryable statuses."""
