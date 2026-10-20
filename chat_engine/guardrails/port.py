"""GuardrailPort — the only extension seam for guardrails (CE-24/E)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from core.contracts.base import JsonObject


class Verdict(StrEnum):
    ALLOW = "allow"
    REDACT = "redact"
    BLOCK = "block"


@dataclass
class GuardrailResult:
    verdict: Verdict
    text: str  # possibly rewritten (redact)
    reason: str | None = None
    details: JsonObject = field(default_factory=dict)


@runtime_checkable
class GuardrailPort(Protocol):
    name: str

    def pre_request(self, text: str) -> GuardrailResult: ...
    def post_response(self, text: str) -> GuardrailResult: ...


__all__ = ["GuardrailPort", "GuardrailResult", "Verdict"]
