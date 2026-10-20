"""Error-detail normalization (CE-24/G) — litellm-style ``details`` enrichment.

ADR-CE-0002 keeps the error *codes* fixed; richer, machine-readable facts go into the
envelope's ``details`` object only. This module computes those facts from a provider's
raw HTTP response and from a normalized ``ProviderError``:

* ``details.rate_limit_type``: ``"requests"`` | ``"tokens"`` | ``"concurrency"`` |
  ``"quota"`` | ``"unknown"`` for 429-class failures;
* ``details.reason = "context_window_exceeded"`` + ``details.max_input_tokens`` /
  ``details.requested_tokens`` when the provider says the prompt is too long. Such a
  failure indicts the REQUEST, not the provider: no retry, no failover, HTTP 422
  ``validation_error`` on the chat surfaces.

The upstream ``ProviderErrorCategory`` enum is immutable, so a context-window overflow
stays ``bad_request``; the marker lives in ``provider_code`` (``CONTEXT_WINDOW_CODE``).
"""

from __future__ import annotations

import re

from core.contracts.base import JsonObject
from core.contracts.provider import ProviderError, ProviderErrorCategory

CONTEXT_WINDOW_CODE = "context_window_exceeded"
CONTEXT_WINDOW_CATEGORY = "context_window"  # breaker-policy key (never counts by default)

_CTX_PATTERNS = (
    re.compile(r"context[_ ]length", re.I),
    re.compile(r"maximum context", re.I),
    re.compile(r"context window", re.I),
    re.compile(r"too many tokens", re.I),
    re.compile(r"prompt is too long", re.I),
    re.compile(r"input is too long", re.I),
    re.compile(r"exceeds? the (?:model'?s )?(?:maximum|max)", re.I),
    re.compile(r"reduce (?:the length of|your prompt)", re.I),
    re.compile(r"string too long", re.I),
)
_CTX_CODES = ("context_length_exceeded", "context_window_exceeded", "max_tokens_exceeded")
#: any integer >= 100 ("128000", "130,123")
_NUM = re.compile(r"\b(\d{3,}(?:,\d{3})*)\b")
#: "maximum context length is 128000" / "limit of 200000"
_MAX = re.compile(
    r"(?:maximum|max(?:imum)?(?: context)?(?: length)?|limit)[^\d]{0,40}(\d[\d,]{2,})", re.I
)
#: "250000 tokens > 200000 maximum" / "200000 token limit"
_MAX_TRAILING = re.compile(r"(\d[\d,]{2,})\s*(?:tokens?\s*)?(?:maximum|max\b|limit)", re.I)
#: "resulted in 130123 tokens" / "requested 9000 tokens"
_REQ = re.compile(
    r"(?:resulted in|requested|got|received|you (?:sent|provided))[^\d]{0,30}(\d[\d,]{2,})", re.I
)


def _int(s: str) -> int | None:
    try:
        return int(s.replace(",", ""))
    except ValueError:
        return None


def is_context_window_message(text: str | None, code: str | None = None) -> bool:
    if code and any(c in code for c in _CTX_CODES):
        return True
    if not text:
        return False
    return any(p.search(text) for p in _CTX_PATTERNS)


def context_window_facts(text: str | None) -> JsonObject:
    """Best-effort ``max_input_tokens`` / ``requested_tokens`` from a provider message.

    Handles "maximum context length is 128000 tokens ... resulted in 130123 tokens"
    (OpenAI), "250000 tokens > 200000 maximum" (Anthropic-style) and bare number pairs
    (smaller = limit, larger = request)."""
    out: JsonObject = {}
    if not text:
        return out
    m = _MAX.search(text) or _MAX_TRAILING.search(text)
    if m and (v := _int(m.group(1))) is not None:
        out["max_input_tokens"] = v
    r = _REQ.search(text)
    if r and (v := _int(r.group(1))) is not None:
        out["requested_tokens"] = v
    nums = [n for n in (_int(x) for x in _NUM.findall(text)) if n is not None]
    if "max_input_tokens" not in out and nums:
        out["max_input_tokens"] = min(nums)
    if "requested_tokens" not in out and len(nums) > 1:
        big = max(nums)
        if big != out.get("max_input_tokens"):
            out["requested_tokens"] = big
    return out


def rate_limit_type(code: str | None, text: str | None, *, status: int | None = None) -> str:
    """Classify a 429 (litellm: ``requests`` vs ``tokens``; plus quota/concurrency)."""
    del status  # reserved: future status-specific heuristics
    blob = f"{code or ''} {text or ''}".lower()
    if any(k in blob for k in ("quota", "billing", "insufficient", "credit", "plan")):
        return "quota"
    if any(k in blob for k in ("concurren", "parallel", "in-flight", "in flight", "simultaneous")):
        return "concurrency"
    if any(k in blob for k in ("tpm", "tokens per", "token rate", "token limit", "tokens/min")):
        return "tokens"
    if any(
        k in blob
        for k in ("rpm", "requests per", "request rate", "too many requests", "requests/min")
    ):
        return "requests"
    return "unknown"


def is_context_window_error(err: ProviderError | None) -> bool:
    if err is None:
        return False
    return err.category is ProviderErrorCategory.BAD_REQUEST and bool(
        err.provider_code and CONTEXT_WINDOW_CODE in err.provider_code
    )


def enrich_details(details: JsonObject, err: ProviderError | None) -> JsonObject:
    """Add CE-24/G facts for a normalized ProviderError (in place; returns ``details``).
    A caller-set ``reason`` is preserved (``cause`` then carries the marker)."""
    if err is None:
        return details
    if is_context_window_error(err):
        details.setdefault("reason", CONTEXT_WINDOW_CODE)
        if details["reason"] != CONTEXT_WINDOW_CODE:
            details.setdefault("cause", CONTEXT_WINDOW_CODE)
        details.setdefault("retryable_by_resizing", True)
        for k, v in context_window_facts(err.safe_message).items():
            details.setdefault(k, v)
    elif err.category in (
        ProviderErrorCategory.RATE_LIMITED,
        ProviderErrorCategory.QUOTA_EXCEEDED,
    ):
        details.setdefault(
            "rate_limit_type",
            "quota"
            if err.category is ProviderErrorCategory.QUOTA_EXCEEDED
            else rate_limit_type(err.provider_code, err.safe_message, status=429),
        )
        if err.retry_after_ms is not None:
            details.setdefault("retry_after_ms", err.retry_after_ms)
    return details


def breaker_category(err: ProviderError | None) -> str | None:
    """Category key used for the admission breaker policy: context-window overflows get
    their own key so ``CE_BREAKER_POLICY_JSON`` can zero them out explicitly."""
    if err is None:
        return None
    if is_context_window_error(err):
        return CONTEXT_WINDOW_CATEGORY
    return err.category.value


__all__ = [
    "CONTEXT_WINDOW_CATEGORY",
    "CONTEXT_WINDOW_CODE",
    "breaker_category",
    "context_window_facts",
    "enrich_details",
    "is_context_window_error",
    "is_context_window_message",
    "rate_limit_type",
]
