"""HandoffPacket v1 — neutral context-handoff contract (ADR-CE-0001 D4).

NEW contracts (chat_engine-owned, ``extra=forbid`` via ContractModel). They
never redefine upstream's ``Message``/``ComposedContext``; the compiler
PROJECTS them onto ``payload["messages"]`` (OpenAI-compatible) while the
legacy keys (``ask``/``context``) stay in the payload for old adapters.

Shape influenced by vercel/ai prompt parts (content parts, tool-call /
tool-result parts) — pattern only, no code (CHAT_ENGINE_REFERENCE_ASSESSMENT).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from core.contracts.base import BoundedStr, ContractModel, JsonObject
from pydantic import Field

HANDOFF_PACKET_VERSION = 1


class CanonicalRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class TextPart(ContractModel):
    type: Literal["text"] = "text"
    text: str = Field(min_length=0, max_length=400_000)


class ImageRefPart(ContractModel):
    type: Literal["image_ref"] = "image_ref"
    ref: BoundedStr
    mime_type: BoundedStr | None = None
    detail: BoundedStr | None = None


class FileRefPart(ContractModel):
    type: Literal["file_ref"] = "file_ref"
    ref: BoundedStr
    name: BoundedStr | None = None
    mime_type: BoundedStr | None = None


class ToolCallPart(ContractModel):
    type: Literal["tool_call"] = "tool_call"
    tool_call_id: BoundedStr
    name: BoundedStr
    arguments: JsonObject = Field(default_factory=dict)


class ToolResultPart(ContractModel):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: BoundedStr
    name: BoundedStr
    result: JsonObject | str
    is_error: bool = False


ContentPart = Annotated[
    TextPart | ImageRefPart | FileRefPart | ToolCallPart | ToolResultPart,
    Field(discriminator="type"),
]


class CanonicalMessage(ContractModel):
    role: CanonicalRole
    parts: list[ContentPart] = Field(default_factory=list)
    name: BoundedStr | None = None
    tool_call_id: BoundedStr | None = None
    #: provenance — never content: "turn:<id>", "message:<id>", "summary:<id>", "request"
    source: BoundedStr | None = None

    def text(self) -> str:
        return "".join(p.text for p in self.parts if isinstance(p, TextPart))

    @classmethod
    def of(
        cls, role: CanonicalRole | str, text: str, *, source: str | None = None
    ) -> CanonicalMessage:
        return cls(role=CanonicalRole(role), parts=[TextPart(text=text)], source=source)


class ToolResult(ContractModel):
    tool_call_id: BoundedStr
    name: BoundedStr
    result: JsonObject | str
    is_error: bool = False


class Decision(ContractModel):
    """A decision already taken in the task — the next model must not re-decide."""

    key: BoundedStr
    value: BoundedStr
    reason: BoundedStr | None = None
    decided_by: BoundedStr | None = None


class AttachmentRef(ContractModel):
    ref: BoundedStr
    name: BoundedStr | None = None
    mime_type: BoundedStr | None = None
    size_bytes: int | None = Field(default=None, ge=0)


class Progress(ContractModel):
    done: list[BoundedStr] = Field(default_factory=list)
    pending: list[BoundedStr] = Field(default_factory=list)
    resume_at: BoundedStr | None = None


class HandoffOutputSpec(ContractModel):
    format: BoundedStr | None = None
    language: BoundedStr | None = None
    schema_: Annotated[JsonObject | None, Field(alias="schema")] = None


class HandoffBudget(ContractModel):
    max_tokens_hint: int | None = Field(default=None, ge=1)
    context_window: int | None = Field(default=None, ge=1)
    reserve_for_output: int = Field(default=1024, ge=0)


class HandoffPacket(ContractModel):
    """Everything model B needs to continue what model A was doing."""

    version: Literal[1] = HANDOFF_PACKET_VERSION
    packet_id: BoundedStr
    objective: str = Field(min_length=0, max_length=100_000)
    constraints: list[BoundedStr] = Field(default_factory=list)
    messages: list[CanonicalMessage] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    attachments: list[AttachmentRef] = Field(default_factory=list)
    progress: Progress = Field(default_factory=Progress)
    #: structured summary of OLDER history — never chain-of-thought
    summary: str | None = Field(default=None, max_length=100_000)
    output_spec: HandoffOutputSpec | None = None
    budget: HandoffBudget = Field(default_factory=HandoffBudget)
    #: generation knobs forwarded verbatim (adapters whitelist)
    generation: JsonObject = Field(default_factory=dict)
    #: skills admitted for this turn (CE-19): [{id, version, name}]
    skills: list[JsonObject] = Field(default_factory=list)


class CompiledHandoff(ContractModel):
    """What the compiler produced + what it dropped (explainable truncation)."""

    messages: list[JsonObject]
    estimated_tokens: int = Field(ge=0)
    dropped_messages: int = Field(default=0, ge=0)
    truncated: bool = False
    notes: list[BoundedStr] = Field(default_factory=list)
