"""CE-19 SkillAssignmentService — assignment + resolution over upstream SkillRegistry.

Rules (recorded in CHAT_ENGINE_TASKS.md CE-19):
- Selectability is upstream's: ``SkillRegistry.select`` (ACTIVE ∧ LOCAL). Never re-implemented.
- Scopes: profile → project → conversation → explicit; the narrowest wins; ``enabled:false``
  at a narrower scope disables what was inherited.
- ``resolve_for_turn`` records the registry version that actually applies; a version pin
  that does not match any registered version of that skill is rejected (422).
- Instruction bodies (14 §2 has none) live in a chat_engine side-table keyed by registry id.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from core.contracts.base import JsonObject
from core.contracts.skills import (
    Skill,
    SkillInvocation,
    SkillListEntry,
    SkillManifest,
    SkillRuntime,
    SkillSource,
    SkillStatus,
    SkillToolRequirements,
    SkillType,
)
from core.roles.errors import SkillNotSelectable

from chat_engine.skills.contracts import (
    SCOPE_PRECEDENCE,
    AgentProfile,
    AgentProfileCreate,
    AgentProfileUpdate,
    AssignmentScope,
    LocalSkillCreate,
    ResolvedSkill,
    SkillAssignment,
    SkillDetail,
    SkillNotSelectableError,
    SkillUnknownError,
)
from chat_engine.util.time import utc_now

if TYPE_CHECKING:
    from chat_engine.composition.runtime import Runtime
    from chat_engine.conversations.contracts import Conversation, Project


def _flat_tools(m: SkillManifest) -> list[str]:
    seen: dict[str, None] = {}
    for t in (*m.requires_tools.required, *m.requires_tools.optional):
        seen.setdefault(t, None)
    return list(seen)


class SkillAssignmentService:
    def __init__(self, rt: Runtime) -> None:
        self._rt = rt
        self._registry = rt.skills  # upstream SkillRegistry
        self._instructions: dict[UUID, str] = {}
        self._profiles: dict[UUID, AgentProfile] = {}

    # -- registry views ---------------------------------------------------------------------------

    def _by_manifest_id(self, skill_id: str) -> list[Skill]:
        return [s for s in self._registry.list_all() if s.manifest.id == skill_id]

    def _pick(self, skill_id: str, version: str | None) -> Skill:
        rows = self._by_manifest_id(skill_id)
        if not rows:
            raise SkillUnknownError(skill_id)
        if version is not None:
            rows = [s for s in rows if s.version == version]
            if not rows:
                raise SkillNotSelectableError(skill_id, f"version {version} is not registered")
        # prefer selectable, then highest version string
        rows.sort(key=lambda s: (s.status is SkillStatus.ACTIVE, s.version), reverse=True)
        chosen = rows[0]
        try:
            self._registry.select(chosen.id)
        except SkillNotSelectable as exc:
            raise SkillNotSelectableError(skill_id, str(exc.args[-1] if exc.args else exc)) from None
        return chosen

    def list_entries(self) -> list[SkillListEntry]:
        """Exactly upstream's GET /v1/skills rows (selectable only, name-ordered)."""
        return [SkillListEntry.from_skill(s) for s in self._registry.list_selectable()]

    def detail(self, skill_id: str) -> SkillDetail:
        rows = self._by_manifest_id(skill_id)
        if not rows:
            raise SkillUnknownError(skill_id)
        rows.sort(key=lambda s: (s.status is SkillStatus.ACTIVE, s.version), reverse=True)
        s = rows[0]
        selectable = any(x.id == s.id for x in self._registry.list_selectable())
        return SkillDetail(
            id=s.manifest.id,
            registry_id=str(s.id),
            name=s.name,
            version=s.version,
            type=s.type.value,
            source=s.source.value,
            status=s.status.value,
            selectable=selectable,
            capabilities=list(s.manifest.capabilities),
            requires_tools=_flat_tools(s.manifest),
            permissions_requested=list(s.manifest.permissions_requested),
            compatible_roles=list(s.manifest.runtime.compatible_roles),
            instructions=self._instructions.get(s.id),
            versions=sorted({x.version for x in rows}),
        )

    def register_local(self, req: LocalSkillCreate) -> SkillDetail:
        manifest = SkillManifest(
            id=req.id,
            name=req.name,
            version=req.version,
            type=SkillType(req.type),
            source=SkillSource.LOCAL,
            status=SkillStatus(req.status),
            capabilities=list(req.capabilities),
            requires_tools=SkillToolRequirements(required=list(req.requires_tools)),
            permissions_requested=list(req.permissions_requested),
            runtime=SkillRuntime(
                invocation=SkillInvocation.USER_OR_MODEL,
                compatible_roles=list(req.compatible_roles),
            ),
        )
        skill = Skill(
            id=uuid4(),
            name=req.name,
            version=req.version,
            type=manifest.type,
            source=SkillSource.LOCAL,
            manifest=manifest,
            status=manifest.status,
        )
        if any(s.version == req.version for s in self._by_manifest_id(req.id)):
            raise SkillNotSelectableError(req.id, f"version {req.version} already registered")
        self._registry.register(skill)
        if req.instructions:
            self._instructions[skill.id] = req.instructions
        return self.detail(req.id)

    def set_status(self, skill_id: str, version: str | None, status: SkillStatus) -> SkillDetail:
        rows = self._by_manifest_id(skill_id)
        if not rows:
            raise SkillUnknownError(skill_id)
        for s in rows:
            if version is None or s.version == version:
                self._registry.replace(
                    s.model_copy(
                        update={"status": status, "manifest": s.manifest.model_copy(update={"status": status})}
                    )
                )
        return self.detail(skill_id)

    # -- agent profiles ---------------------------------------------------------------------------

    def create_profile(self, req: AgentProfileCreate) -> AgentProfile:
        self._validate_assignments(req.skills)
        now = utc_now()
        p = AgentProfile(
            id=uuid4(), tenant_id=self._rt.tenant_id, created_at=now, updated_at=now, **req.model_dump()
        )
        self._profiles[p.id] = p
        return p

    def update_profile(self, profile_id: UUID, req: AgentProfileUpdate) -> AgentProfile:
        p = self.get_profile(profile_id)
        if req.skills is not None:
            self._validate_assignments(req.skills)
        p = p.model_copy(update={**req.model_dump(exclude_none=True), "updated_at": utc_now()})
        self._profiles[p.id] = p
        return p

    def get_profile(self, profile_id: UUID) -> AgentProfile:
        p = self._profiles.get(profile_id)
        if p is None or p.tenant_id != self._rt.tenant_id:
            raise SkillUnknownError(f"agent_profile:{profile_id}")
        return p

    def list_profiles(self) -> list[AgentProfile]:
        return sorted(
            (p for p in self._profiles.values() if p.tenant_id == self._rt.tenant_id),
            key=lambda p: p.created_at,
        )

    def delete_profile(self, profile_id: UUID) -> None:
        self.get_profile(profile_id)
        del self._profiles[profile_id]

    def _validate_assignments(self, rows: list[SkillAssignment]) -> None:
        for a in rows:
            if a.enabled:
                self._pick(a.skill_id, a.version)  # raises Unknown / NotSelectable
            elif not self._by_manifest_id(a.skill_id):
                raise SkillUnknownError(a.skill_id)

    @staticmethod
    def upsert(rows: list[SkillAssignment], new: SkillAssignment) -> list[SkillAssignment]:
        out = [r for r in rows if r.skill_id != new.skill_id]
        out.append(new)
        return out

    # -- resolution --------------------------------------------------------------------------------

    @staticmethod
    def _as_assignments(raw: list[Any]) -> list[SkillAssignment]:
        out: list[SkillAssignment] = []
        for r in raw:
            if isinstance(r, SkillAssignment):
                out.append(r)
            elif isinstance(r, dict):
                out.append(SkillAssignment.model_validate(r))
            elif isinstance(r, str):
                out.append(SkillAssignment(skill_id=r))
        return out

    def resolve(
        self,
        *,
        profile: list[SkillAssignment] | None = None,
        project: list[SkillAssignment] | None = None,
        conversation: list[SkillAssignment] | None = None,
        explicit: list[str] | None = None,
    ) -> list[ResolvedSkill]:
        """Merge scopes (narrowest wins) → only enabled, selectable skills, with versions."""
        layers: list[tuple[AssignmentScope, list[SkillAssignment]]] = [
            (AssignmentScope.PROFILE, profile or []),
            (AssignmentScope.PROJECT, project or []),
            (AssignmentScope.CONVERSATION, conversation or []),
            (AssignmentScope.EXPLICIT, [SkillAssignment(skill_id=s) for s in (explicit or [])]),
        ]
        if explicit is not None:
            # explicit selection WINS outright: it replaces inherited sets (owner requirement)
            layers = [(AssignmentScope.EXPLICIT, layers[-1][1])]
        effective: dict[str, tuple[AssignmentScope, SkillAssignment]] = {}
        for scope, rows in sorted(layers, key=lambda t: SCOPE_PRECEDENCE.index(t[0])):
            for a in rows:
                effective[a.skill_id] = (scope, a)
        out: list[ResolvedSkill] = []
        for skill_id, (scope, a) in effective.items():
            if not a.enabled:
                continue
            s = self._pick(skill_id, a.version)
            out.append(
                ResolvedSkill(
                    skill_id=skill_id,
                    version=s.version,
                    name=s.name,
                    type=s.type.value,
                    source=scope,
                    registry_id=str(s.id),
                    requested_version=a.version,
                    config=dict(a.config),
                    instructions=self._instructions.get(s.id),
                    requires_tools=_flat_tools(s.manifest),
                )
            )
        out.sort(key=lambda r: r.skill_id)
        return out

    def resolve_for_turn(
        self,
        *,
        conversation: Conversation | None,
        project: Project | None,
        explicit: list[str] | None,
    ) -> list[JsonObject]:
        """ChatService hook (CE-09 `_effective_skills`). Returns JSON records for
        ``Turn.skills_used`` and ``HandoffPacket.skills`` (``id``/``version``/``name``
        + full ResolvedSkill fields)."""
        profile_rows: list[SkillAssignment] = []
        if project is not None and project.agent_profile_id:
            try:
                profile_rows = list(self.get_profile(UUID(project.agent_profile_id)).skills)
            except (ValueError, SkillUnknownError):
                profile_rows = []
        resolved = self.resolve(
            profile=profile_rows,
            project=self._as_assignments(list(project.skills)) if project else None,
            conversation=self._as_assignments(list(conversation.skills)) if conversation else None,
            explicit=explicit,
        )
        return [
            {"id": r.skill_id, **r.model_dump(mode="json", exclude_none=True)} for r in resolved
        ]


__all__ = ["SkillAssignmentService"]
