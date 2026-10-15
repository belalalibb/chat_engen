"""Conversations / Projects / Turns (CE-09) — chat_engine-owned contracts.

Upstream's ``Conversation``/``Message`` (core.contracts.conversation) stay in
use for the legacy ``/v1/execute`` path. These contracts are the chat
surface: turns carry PARTS (not a string), provider/model provenance, usage,
and the HandoffPacket id that produced them.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from core.contracts.base import BoundedStr, ContractModel, JsonObject
from pydantic import Field

from chat_engine.handoff.contracts import CanonicalRole, ContentPart


class TurnKind(StrEnum):
    MESSAGE = "message"
    SUMMARY = "summary"
    SYSTEM = "system"
    TOOL = "tool"


class TurnStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"  # interrupted stream — kept honestly
    FAILED = "failed"


class Project(ContractModel):
    id: UUID
    tenant_id: UUID
    name: BoundedStr
    system_instructions: str | None = Field(default=None, max_length=100_000)
    default_model: BoundedStr | None = None  # canonical_id or model_key
    default_constraints: list[BoundedStr] = Field(default_factory=list)
    summarize_with: BoundedStr | None = None
    context_window_hint: int | None = Field(default=None, ge=1)
    #: Skills ASSIGNED to this project (CE-19). Entries: {skill_id, version, enabled}
    skills: list[JsonObject] = Field(default_factory=list)
    agent_profile_id: BoundedStr | None = None
    created_at: datetime
    updated_at: datetime
    metadata: JsonObject = Field(default_factory=dict)


class Conversation(ContractModel):
    id: UUID
    tenant_id: UUID
    user_id: UUID
    project_id: UUID | None = None
    title: BoundedStr
    model: BoundedStr | None = None
    objective: str | None = Field(default=None, max_length=100_000)
    constraints: list[BoundedStr] = Field(default_factory=list)
    #: per-conversation skill overrides (CE-19): {skill_id, version, enabled}
    skills: list[JsonObject] = Field(default_factory=list)
    turn_count: int = 0
    summary_turn_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    archived: bool = False
    metadata: JsonObject = Field(default_factory=dict)


class Turn(ContractModel):
    id: UUID
    conversation_id: UUID
    seq: int = Field(ge=0)
    kind: TurnKind = TurnKind.MESSAGE
    role: CanonicalRole
    parts: list[ContentPart] = Field(default_factory=list)
    status: TurnStatus = TurnStatus.COMPLETE
    provider_key: BoundedStr | None = None
    model_key: BoundedStr | None = None
    canonical_id: BoundedStr | None = None
    usage: JsonObject = Field(default_factory=dict)
    latency_ms: int | None = None
    handoff_packet_id: BoundedStr | None = None
    execution_id: BoundedStr | None = None
    stream_id: BoundedStr | None = None
    #: skills in effect for this turn (CE-19): [{id, version}]
    skills_used: list[JsonObject] = Field(default_factory=list)
    created_at: datetime
    metadata: JsonObject = Field(default_factory=dict)

    def text(self) -> str:
        from chat_engine.handoff.contracts import TextPart

        return "".join(p.text for p in self.parts if isinstance(p, TextPart))


# --- API request/response shapes -------------------------------------------------------


class ProjectCreate(ContractModel):
    name: BoundedStr
    system_instructions: str | None = Field(default=None, max_length=100_000)
    default_model: BoundedStr | None = None
    default_constraints: list[BoundedStr] = Field(default_factory=list)
    summarize_with: BoundedStr | None = None
    context_window_hint: int | None = Field(default=None, ge=1)
    skills: list[JsonObject] = Field(default_factory=list)
    agent_profile_id: BoundedStr | None = None
    metadata: JsonObject = Field(default_factory=dict)


class ProjectUpdate(ContractModel):
    name: BoundedStr | None = None
    system_instructions: str | None = Field(default=None, max_length=100_000)
    default_model: BoundedStr | None = None
    default_constraints: list[BoundedStr] | None = None
    summarize_with: BoundedStr | None = None
    context_window_hint: int | None = Field(default=None, ge=1)
    skills: list[JsonObject] | None = None
    agent_profile_id: BoundedStr | None = None
    metadata: JsonObject | None = None


class ConversationCreate(ContractModel):
    title: BoundedStr | None = None
    project_id: UUID | None = None
    model: BoundedStr | None = None
    objective: str | None = Field(default=None, max_length=100_000)
    constraints: list[BoundedStr] = Field(default_factory=list)
    skills: list[JsonObject] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


class ConversationUpdate(ContractModel):
    title: BoundedStr | None = None
    model: BoundedStr | None = None
    objective: str | None = Field(default=None, max_length=100_000)
    constraints: list[BoundedStr] | None = None
    skills: list[JsonObject] | None = None
    archived: bool | None = None
    metadata: JsonObject | None = None


class ChatRequest(ContractModel):
    """One user turn into a conversation (sync). Streaming: /v1/chat/stream with conversation_id."""

    conversation_id: UUID | None = None
    project_id: UUID | None = None
    message: str = Field(min_length=1, max_length=400_000)
    model: BoundedStr | None = None
    provider: BoundedStr | None = None
    generation: JsonObject = Field(default_factory=dict)
    tools: list[JsonObject] = Field(default_factory=list)
    required_capabilities: list[BoundedStr] = Field(default_factory=list)
    skills: list[BoundedStr] | None = (
        None  # explicit selection wins over project/conversation assignment
    )
    attachments: list[JsonObject] = Field(default_factory=list)
    strategy: BoundedStr | None = None  # CE-12: sequential_handoff | judge_select | cost_tiered
    strategy_options: JsonObject = Field(default_factory=dict)


class ChatResponse(ContractModel):
    conversation_id: UUID
    turn: Turn
    user_turn_id: UUID
    execution_id: BoundedStr | None = None
    provider_key: BoundedStr | None = None
    model_key: BoundedStr | None = None
    usage: JsonObject = Field(default_factory=dict)
    handoff: JsonObject = Field(
        default_factory=dict
    )  # {packet_id, estimated_tokens, dropped_messages, summarized}
    trace: JsonObject | None = None  # CE-12 orchestration trace


class TurnsPage(ContractModel):
    turns: list[Turn]
    total: int
    conversation_id: UUID


class ConversationsPage(ContractModel):
    conversations: list[Conversation]
    total: int


class ProjectsPage(ContractModel):
    projects: list[Project]
    total: int
