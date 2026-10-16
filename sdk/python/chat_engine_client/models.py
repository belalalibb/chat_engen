"""Lightweight typed views (dataclasses, no pydantic) over API JSON."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Turn:
    id: str
    seq: int
    role: str
    kind: str
    status: str
    text: str
    provider_key: str | None
    model_key: str | None
    usage: dict[str, Any]
    skills_used: list[dict[str, Any]]
    raw: dict[str, Any] = field(repr=False)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Turn:
        return cls(
            id=d["id"],
            seq=int(d["seq"]),
            role=d["role"],
            kind=d.get("kind", "message"),
            status=d.get("status", "complete"),
            text="".join(p.get("text", "") for p in d.get("parts", []) if p.get("type") == "text"),
            provider_key=d.get("provider_key"),
            model_key=d.get("model_key"),
            usage=dict(d.get("usage") or {}),
            skills_used=list(d.get("skills_used") or []),
            raw=d,
        )


@dataclass(frozen=True)
class ChatResult:
    conversation_id: str
    turn: Turn
    execution_id: str | None
    provider_key: str | None
    model_key: str | None
    usage: dict[str, Any]
    handoff: dict[str, Any]
    raw: dict[str, Any] = field(repr=False)

    @property
    def text(self) -> str:
        return self.turn.text

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> ChatResult:
        return cls(
            conversation_id=d["conversation_id"],
            turn=Turn.from_json(d["turn"]),
            execution_id=d.get("execution_id"),
            provider_key=d.get("provider_key"),
            model_key=d.get("model_key"),
            usage=dict(d.get("usage") or {}),
            handoff=dict(d.get("handoff") or {}),
            raw=d,
        )


@dataclass(frozen=True)
class StreamEvent:
    seq: int
    type: str
    stream_id: str
    data: dict[str, Any]
    event_id: str | None = None

    @property
    def delta(self) -> str:
        return str(self.data.get("delta", "")) if self.type == "message_delta" else ""

    @property
    def terminal(self) -> bool:
        return self.type in ("completed", "failed", "cancelled")


@dataclass(frozen=True)
class Completion:
    """OpenAI-compatible completion, minimal view."""

    id: str
    model: str
    content: str | None
    finish_reason: str | None
    usage: dict[str, Any]
    tool_calls: list[dict[str, Any]]
    raw: dict[str, Any] = field(repr=False)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Completion:
        ch = (d.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        return cls(
            id=d["id"],
            model=d["model"],
            content=msg.get("content"),
            finish_reason=ch.get("finish_reason"),
            usage=dict(d.get("usage") or {}),
            tool_calls=list(msg.get("tool_calls") or []),
            raw=d,
        )
