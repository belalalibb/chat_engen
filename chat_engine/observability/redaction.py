"""Redaction for logs/spans: secrets patterns + message content never leave the process."""

from __future__ import annotations

import re
from typing import Any

SECRET_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]
SENSITIVE_KEYS = {"secret", "api_key", "apikey", "authorization", "password", "token", "credential", "messages", "content", "ask", "message", "text", "delta", "instructions", "system_instructions"}
MASK = "[REDACTED]"


def redact_text(s: str) -> str:
    for pat in SECRET_PATTERNS:
        s = pat.sub(MASK, s)
    return s


def redact(obj: Any, *, _depth: int = 0) -> Any:
    """Deep-copy with secret patterns masked and content-bearing keys dropped."""
    if _depth > 12:
        return MASK
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in SENSITIVE_KEYS or lk.endswith("_key") or lk.endswith("_secret"):
                out[str(k)] = MASK if not isinstance(v, list | dict) else f"{MASK}({len(v)})"
            else:
                out[str(k)] = redact(v, _depth=_depth + 1)
        return out
    if isinstance(obj, list | tuple):
        return [redact(v, _depth=_depth + 1) for v in obj]
    return obj


__all__ = ["MASK", "SECRET_PATTERNS", "SENSITIVE_KEYS", "redact", "redact_text"]
