"""Streaming contracts (ADR-CE-0001 D7).

``StreamingAdapterPort`` is a NEW, OPTIONAL port — ``ProviderAdapterPort`` and
``generate`` are untouched. Event vocabulary follows start/delta/end parts
(vercel/ai pattern): every event carries ``seq`` and ``event_id`` so a client
can resume with ``delivered_until_seq``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Protocol, runtime_checkable

from core.contracts.base import BoundedStr, ContractModel, JsonObject
from core.contracts.provider import ProviderError, ProviderGenerateRequest
from pydantic import Field


class StreamEventType(StrEnum):
    STARTED = "started"
    MESSAGE_DELTA = "message_delta"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_DELTA = "tool_call_delta"
    TOOL_CALL_END = "tool_call_end"
    USAGE = "usage"
    HEARTBEAT = "heartbeat"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StreamChunk(ContractModel):
    """What an adapter yields. Exactly one of the payload fields is set."""

    delta: str | None = None
    tool_call: JsonObject | None = None  # {index, id?, name?, arguments_delta?}
    usage: JsonObject | None = None
    finish_reason: BoundedStr | None = None
    error: ProviderError | None = None
    raw_model: BoundedStr | None = None


class StreamEvent(ContractModel):
    seq: int = Field(ge=0)
    event_id: BoundedStr
    type: StreamEventType
    stream_id: BoundedStr
    data: JsonObject = Field(default_factory=dict)


class StreamFailureReason(StrEnum):
    STREAM_INTERRUPTED = "stream_interrupted"
    PROVIDER_ERROR = "provider_error"
    NO_CANDIDATE = "no_candidate"
    FIRST_BYTE_TIMEOUT = "first_byte_timeout"
    IDLE_TIMEOUT = "idle_timeout"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    UNSUPPORTED = "unsupported_capability"
    ADMISSION_DENIED = "admission_denied"
    VALIDATION = "validation_error"  # CE-24/G: request-indicting (context window)


@runtime_checkable
class StreamingAdapterPort(Protocol):
    """Optional capability: an adapter that can stream a ``ProviderGenerateRequest``."""

    def stream(self, request: ProviderGenerateRequest) -> AsyncIterator[StreamChunk]: ...


class StreamTimeouts(ContractModel):
    first_byte_ms: int = Field(default=30_000, ge=1)
    idle_ms: int = Field(default=60_000, ge=1)
    total_ms: int | None = Field(default=None, ge=1_000)
    heartbeat_ms: int = Field(default=15_000, ge=1_000)


class StreamSummary(ContractModel):
    stream_id: BoundedStr
    execution_id: BoundedStr | None = None
    provider_key: BoundedStr | None = None
    model_key: BoundedStr | None = None
    status: StreamEventType
    delivered_seq: int = 0
    text: str = ""
    tool_calls: list[JsonObject] = Field(default_factory=list)
    usage: JsonObject = Field(default_factory=dict)
    synthesized: bool = False
    failovers: int = 0
    reason: StreamFailureReason | None = None
    resumable: bool = False
