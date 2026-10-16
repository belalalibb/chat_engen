"""CE-19 — skills API.

- ``GET /v1/skills``: upstream 10 §7 shape, byte-compatible (selectable only).
- ``GET /v1/skills/{id}``: detail (manifest + status + versions + instruction body).
- ``/v1/agent-profiles``: CRUD of reusable skill bundles.
- ``/v1/projects/{id}/skills`` and ``/v1/conversations/{id}/skills``: GET effective,
  PUT replace, POST ``:assign`` upsert one, POST ``:enable`` / ``:disable``, DELETE one.
- ``/v1/admin/skills``: register a LOCAL skill, ``:enable`` / ``:disable`` (status).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from core.contracts.skills import SkillsListResponse, SkillStatus
from fastapi import APIRouter, Body, Response
from fastapi.responses import JSONResponse

from chat_engine.api.errors import ChatEngineErrorCode, ce_error_response
from chat_engine.composition.runtime import Runtime
from chat_engine.conversations import ConversationUpdate, NotFound, ProjectUpdate
from chat_engine.skills import (
    AgentProfileCreate,
    AgentProfileUpdate,
    AssignRequest,
    LocalSkillCreate,
    SkillAssignment,
    SkillAssignmentService,
    SkillNotSelectableError,
    SkillUnknownError,
)


def _json(model: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content=model.model_dump(mode="json", exclude_none=True)
    )


def _err(exc: Exception) -> JSONResponse:
    if isinstance(exc, SkillUnknownError):
        return ce_error_response(
            ChatEngineErrorCode.NOT_FOUND, str(exc), details={"skill_id": exc.skill_id}
        )
    if isinstance(exc, SkillNotSelectableError):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "skill_not_selectable",
                    "message": str(exc),
                    "retryable": False,
                    "details": {"skill_id": exc.skill_id, "reason": exc.reason},
                    "trace_id": None,
                }
            },
        )
    if isinstance(exc, NotFound):
        return ce_error_response(
            ChatEngineErrorCode.NOT_FOUND,
            f"{exc.kind} not found.",
            details={"kind": exc.kind, "id": str(exc.ident)},
        )
    raise exc


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(tags=["skills"])
    svc: SkillAssignmentService = rt.extensions["skills"]
    chat = rt.extensions["chat"]

    # -- upstream-shaped listing + detail -------------------------------------------------------

    @router.get("/v1/skills")
    async def list_skills() -> Response:
        return _json(SkillsListResponse(skills=svc.list_entries()))

    @router.get("/v1/skills/{skill_id}")
    async def skill_detail(skill_id: str) -> Response:
        try:
            return _json(svc.detail(skill_id))
        except SkillUnknownError as exc:
            return _err(exc)

    # -- admin: local registration / status -----------------------------------------------------

    @router.post("/v1/admin/skills", status_code=201)
    async def register_local(body: LocalSkillCreate) -> Response:
        try:
            return _json(svc.register_local(body), 201)
        except (SkillNotSelectableError, ValueError) as exc:
            if isinstance(exc, SkillNotSelectableError):
                return _err(exc)
            return ce_error_response(ChatEngineErrorCode.CONFLICT, str(exc), http_status=422)

    @router.post("/v1/admin/skills/{skill_id}:enable")
    async def enable_skill(skill_id: str, version: str | None = None) -> Response:
        try:
            return _json(svc.set_status(skill_id, version, SkillStatus.ACTIVE))
        except SkillUnknownError as exc:
            return _err(exc)

    @router.post("/v1/admin/skills/{skill_id}:disable")
    async def disable_skill(skill_id: str, version: str | None = None) -> Response:
        try:
            return _json(svc.set_status(skill_id, version, SkillStatus.DISABLED))
        except SkillUnknownError as exc:
            return _err(exc)

    # -- agent profiles -------------------------------------------------------------------------

    @router.post("/v1/agent-profiles", status_code=201)
    async def create_profile(body: AgentProfileCreate) -> Response:
        try:
            return _json(svc.create_profile(body), 201)
        except (SkillUnknownError, SkillNotSelectableError) as exc:
            return _err(exc)

    @router.get("/v1/agent-profiles")
    async def list_profiles() -> Response:
        items = svc.list_profiles()
        return JSONResponse(
            content={
                "profiles": [p.model_dump(mode="json", exclude_none=True) for p in items],
                "total": len(items),
            }
        )

    @router.get("/v1/agent-profiles/{profile_id}")
    async def get_profile(profile_id: UUID) -> Response:
        try:
            return _json(svc.get_profile(profile_id))
        except SkillUnknownError as exc:
            return _err(exc)

    @router.patch("/v1/agent-profiles/{profile_id}")
    async def update_profile(profile_id: UUID, body: AgentProfileUpdate) -> Response:
        try:
            return _json(svc.update_profile(profile_id, body))
        except (SkillUnknownError, SkillNotSelectableError) as exc:
            return _err(exc)

    @router.delete("/v1/agent-profiles/{profile_id}", status_code=204)
    async def delete_profile(profile_id: UUID) -> Response:
        try:
            svc.delete_profile(profile_id)
        except SkillUnknownError as exc:
            return _err(exc)
        return Response(status_code=204)

    # -- scoped assignments (project / conversation) --------------------------------------------

    def _rows(obj: Any) -> list[SkillAssignment]:
        return svc._as_assignments(list(obj.skills))  # noqa: SLF001 — same package family

    def _effective(project: Any, conversation: Any) -> JSONResponse:
        try:
            resolved = svc.resolve_for_turn(conversation=conversation, project=project, explicit=None)
        except (SkillUnknownError, SkillNotSelectableError) as exc:
            return _err(exc)
        return JSONResponse(
            content={
                "assigned": [a.model_dump(mode="json") for a in _rows(conversation or project)],
                "effective": resolved,
            }
        )

    def _save_project(project_id: UUID, rows: list[SkillAssignment]) -> Any:
        return chat.update_project(
            project_id, ProjectUpdate(skills=[r.model_dump(mode="json") for r in rows])
        )

    def _save_conversation(conversation_id: UUID, rows: list[SkillAssignment]) -> Any:
        return chat.update_conversation(
            conversation_id, ConversationUpdate(skills=[r.model_dump(mode="json") for r in rows])
        )

    def _mutate(rows: list[SkillAssignment], action: str, body: AssignRequest | None, skill_id: str | None) -> list[SkillAssignment]:
        if action == "assign":
            assert body is not None
            new = SkillAssignment(**body.model_dump())
            if new.enabled:
                svc._pick(new.skill_id, new.version)  # noqa: SLF001
            return svc.upsert(rows, new)
        assert skill_id is not None
        if action == "remove":
            return [r for r in rows if r.skill_id != skill_id]
        enabled = action == "enable"
        existing = next((r for r in rows if r.skill_id == skill_id), None)
        if existing is None:
            if enabled:
                svc._pick(skill_id, None)  # noqa: SLF001
            existing = SkillAssignment(skill_id=skill_id)
        return svc.upsert(rows, existing.model_copy(update={"enabled": enabled}))

    # projects
    @router.get("/v1/projects/{project_id}/skills")
    async def project_skills(project_id: UUID) -> Response:
        try:
            p = chat.get_project(project_id)
        except NotFound as exc:
            return _err(exc)
        return _effective(p, None)

    @router.put("/v1/projects/{project_id}/skills")
    async def project_skills_replace(project_id: UUID, body: list[AssignRequest]) -> Response:
        try:
            rows = [SkillAssignment(**b.model_dump()) for b in body]
            svc._validate_assignments(rows)  # noqa: SLF001
            p = _save_project(project_id, rows)
        except (NotFound, SkillUnknownError, SkillNotSelectableError) as exc:
            return _err(exc)
        return _effective(p, None)

    @router.post("/v1/projects/{project_id}/skills:assign")
    async def project_skill_assign(project_id: UUID, body: AssignRequest) -> Response:
        try:
            p = chat.get_project(project_id)
            p = _save_project(project_id, _mutate(_rows(p), "assign", body, None))
        except (NotFound, SkillUnknownError, SkillNotSelectableError) as exc:
            return _err(exc)
        return _effective(p, None)

    @router.post("/v1/projects/{project_id}/skills/{skill_id}:enable")
    async def project_skill_enable(project_id: UUID, skill_id: str) -> Response:
        try:
            p = chat.get_project(project_id)
            p = _save_project(project_id, _mutate(_rows(p), "enable", None, skill_id))
        except (NotFound, SkillUnknownError, SkillNotSelectableError) as exc:
            return _err(exc)
        return _effective(p, None)

    @router.post("/v1/projects/{project_id}/skills/{skill_id}:disable")
    async def project_skill_disable(project_id: UUID, skill_id: str) -> Response:
        try:
            p = chat.get_project(project_id)
            p = _save_project(project_id, _mutate(_rows(p), "disable", None, skill_id))
        except (NotFound, SkillUnknownError, SkillNotSelectableError) as exc:
            return _err(exc)
        return _effective(p, None)

    @router.delete("/v1/projects/{project_id}/skills/{skill_id}")
    async def project_skill_remove(project_id: UUID, skill_id: str) -> Response:
        try:
            p = chat.get_project(project_id)
            p = _save_project(project_id, _mutate(_rows(p), "remove", None, skill_id))
        except NotFound as exc:
            return _err(exc)
        return _effective(p, None)

    # conversations
    def _conv_ctx(conversation_id: UUID) -> tuple[Any, Any]:
        c = chat.get_conversation(conversation_id)
        p = chat.get_project(c.project_id) if c.project_id else None
        return c, p

    @router.get("/v1/conversations/{conversation_id}/skills")
    async def conversation_skills(conversation_id: UUID) -> Response:
        try:
            c, p = _conv_ctx(conversation_id)
        except NotFound as exc:
            return _err(exc)
        return _effective(p, c)

    @router.put("/v1/conversations/{conversation_id}/skills")
    async def conversation_skills_replace(
        conversation_id: UUID, body: list[AssignRequest] = Body(default=[])
    ) -> Response:
        try:
            rows = [SkillAssignment(**b.model_dump()) for b in body]
            svc._validate_assignments(rows)  # noqa: SLF001
            _save_conversation(conversation_id, rows)
            c, p = _conv_ctx(conversation_id)
        except (NotFound, SkillUnknownError, SkillNotSelectableError) as exc:
            return _err(exc)
        return _effective(p, c)

    @router.post("/v1/conversations/{conversation_id}/skills:assign")
    async def conversation_skill_assign(conversation_id: UUID, body: AssignRequest) -> Response:
        try:
            c, _ = _conv_ctx(conversation_id)
            _save_conversation(conversation_id, _mutate(_rows(c), "assign", body, None))
            c, p = _conv_ctx(conversation_id)
        except (NotFound, SkillUnknownError, SkillNotSelectableError) as exc:
            return _err(exc)
        return _effective(p, c)

    @router.post("/v1/conversations/{conversation_id}/skills/{skill_id}:enable")
    async def conversation_skill_enable(conversation_id: UUID, skill_id: str) -> Response:
        try:
            c, _ = _conv_ctx(conversation_id)
            _save_conversation(conversation_id, _mutate(_rows(c), "enable", None, skill_id))
            c, p = _conv_ctx(conversation_id)
        except (NotFound, SkillUnknownError, SkillNotSelectableError) as exc:
            return _err(exc)
        return _effective(p, c)

    @router.post("/v1/conversations/{conversation_id}/skills/{skill_id}:disable")
    async def conversation_skill_disable(conversation_id: UUID, skill_id: str) -> Response:
        try:
            c, _ = _conv_ctx(conversation_id)
            _save_conversation(conversation_id, _mutate(_rows(c), "disable", None, skill_id))
            c, p = _conv_ctx(conversation_id)
        except (NotFound, SkillUnknownError, SkillNotSelectableError) as exc:
            return _err(exc)
        return _effective(p, c)

    @router.delete("/v1/conversations/{conversation_id}/skills/{skill_id}")
    async def conversation_skill_remove(conversation_id: UUID, skill_id: str) -> Response:
        try:
            c, _ = _conv_ctx(conversation_id)
            _save_conversation(conversation_id, _mutate(_rows(c), "remove", None, skill_id))
            c, p = _conv_ctx(conversation_id)
        except NotFound as exc:
            return _err(exc)
        return _effective(p, c)

    return router


__all__ = ["build_router"]
