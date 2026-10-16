"""CE-19 Assignable Skills — contracts.

Upstream owns the Skill entity/manifest/registry (``core.contracts.skills``,
``core.roles.registry.SkillRegistry``); chat_engine adds ASSIGNMENT on top:
who gets which skill, at which scope, enabled or not, and which version actually ran.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from core.contracts.base import BoundedStr, ContractModel, JsonObject
from pydantic import Field


class AssignmentScope(StrEnum):
    PROFILE = "profile"  # AgentProfile (reusable bundle)
    PROJECT = "project"
    CONVERSATION = "conversation"
    EXPLICIT = "explicit"  # per-turn `skills: [...]` on the request


#: Narrowest scope wins when the same skill appears at several levels.
SCOPE_PRECEDENCE: tuple[AssignmentScope, ...] = (
    AssignmentScope.PROFILE,
    AssignmentScope.PROJECT,
    AssignmentScope.CONVERSATION,
    AssignmentScope.EXPLICIT,
)


class SkillAssignment(ContractModel):
    """One assignment row as stored on a profile / project / conversation."""

    skill_id: BoundedStr  # manifest id (stable human key, e.g. ``code_review``)
    version: BoundedStr | None = None  # pin; None ⇒ whatever the registry serves
    enabled: bool = True
    config: JsonObject = Field(default_factory=dict)  # free-form per-assignment settings


class AgentProfile(ContractModel):
    id: UUID
    tenant_id: UUID
    name: BoundedStr
    description: str | None = Field(default=None, max_length=10_000)
    system_instructions: str | None = Field(default=None, max_length=100_000)
    default_model: BoundedStr | None = None
    skills: list[SkillAssignment] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    metadata: JsonObject = Field(default_factory=dict)


class AgentProfileCreate(ContractModel):
    name: BoundedStr
    description: str | None = Field(default=None, max_length=10_000)
    system_instructions: str | None = Field(default=None, max_length=100_000)
    default_model: BoundedStr | None = None
    skills: list[SkillAssignment] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


class AgentProfileUpdate(ContractModel):
    name: BoundedStr | None = None
    description: str | None = Field(default=None, max_length=10_000)
    system_instructions: str | None = Field(default=None, max_length=100_000)
    default_model: BoundedStr | None = None
    skills: list[SkillAssignment] | None = None
    metadata: JsonObject | None = None


class AssignRequest(ContractModel):
    """PUT …/skills — replace the assignment list; POST …/skills:assign — upsert one."""

    skill_id: BoundedStr
    version: BoundedStr | None = None
    enabled: bool = True
    config: JsonObject = Field(default_factory=dict)


class ResolvedSkill(ContractModel):
    """What actually applies to a turn — recorded verbatim in ``Turn.skills_used``."""

    skill_id: BoundedStr
    version: BoundedStr  # the registry version that ran (pin honoured if it matches)
    name: BoundedStr
    type: BoundedStr  # instruction | workflow | tool_enabled
    source: AssignmentScope
    registry_id: BoundedStr  # upstream Skill.id (UUID) for audit
    requested_version: BoundedStr | None = None
    config: JsonObject = Field(default_factory=dict)
    instructions: str | None = None  # instruction-type body injected into the system prompt
    requires_tools: list[BoundedStr] = Field(default_factory=list)


class SkillDetail(ContractModel):
    """GET /v1/skills/{id} — manifest + registry status + instruction body if local."""

    id: BoundedStr
    registry_id: BoundedStr
    name: BoundedStr
    version: BoundedStr
    type: BoundedStr
    source: BoundedStr
    status: BoundedStr
    selectable: bool
    capabilities: list[BoundedStr] = Field(default_factory=list)
    requires_tools: list[BoundedStr] = Field(default_factory=list)
    permissions_requested: list[BoundedStr] = Field(default_factory=list)
    compatible_roles: list[BoundedStr] = Field(default_factory=list)
    instructions: str | None = None
    versions: list[BoundedStr] = Field(default_factory=list)


class LocalSkillCreate(ContractModel):
    """POST /v1/admin/skills — register a LOCAL skill (manifest 14 §2 subset)."""

    id: BoundedStr
    name: BoundedStr
    version: BoundedStr = "1.0.0"
    type: BoundedStr = "instruction"
    instructions: str | None = Field(default=None, max_length=100_000)
    capabilities: list[BoundedStr] = Field(default_factory=list)
    requires_tools: list[BoundedStr] = Field(default_factory=list)
    permissions_requested: list[BoundedStr] = Field(default_factory=list)
    compatible_roles: list[BoundedStr] = Field(default_factory=list)
    status: BoundedStr = "active"  # admins may register as disabled/approved for review flows


class SkillNotSelectableError(Exception):
    def __init__(self, skill_id: str, reason: str) -> None:
        self.skill_id, self.reason = skill_id, reason
        super().__init__(f"skill '{skill_id}' not selectable: {reason}")


class SkillUnknownError(LookupError):
    def __init__(self, skill_id: str) -> None:
        self.skill_id = skill_id
        super().__init__(f"skill '{skill_id}' is not registered")


__all__ = [
    "SCOPE_PRECEDENCE",
    "AgentProfile",
    "AgentProfileCreate",
    "AgentProfileUpdate",
    "AssignRequest",
    "AssignmentScope",
    "LocalSkillCreate",
    "ResolvedSkill",
    "SkillAssignment",
    "SkillDetail",
    "SkillNotSelectableError",
    "SkillUnknownError",
]
