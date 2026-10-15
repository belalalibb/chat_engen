"""CE-09 — /v1/projects, /v1/conversations, /v1/conversations/{id}/turns, POST /v1/chat.

All business logic lives in ``chat_engine.conversations.ChatService``; this module
only maps HTTP ⇄ contracts and ``ChatError``/``NotFound`` ⇄ the unified envelope.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query, Response
from fastapi.responses import JSONResponse

from chat_engine.api.errors import ChatEngineErrorCode, ce_error_response
from chat_engine.composition.runtime import Runtime
from chat_engine.conversations import (
    ChatError,
    ChatRequest,
    ChatService,
    ConversationCreate,
    ConversationsPage,
    ConversationUpdate,
    NotFound,
    ProjectCreate,
    ProjectsPage,
    ProjectUpdate,
    TurnsPage,
)

_CE_CODES = {c.value for c in ChatEngineErrorCode}


def chat_error_response(exc: ChatError) -> JSONResponse:
    """ChatError → envelope. Codes outside ChatEngineErrorCode (e.g. execution_failed)
    keep their own string so clients see the precise cause."""
    if exc.code in _CE_CODES:
        return ce_error_response(
            ChatEngineErrorCode(exc.code),
            exc.message,
            details=exc.details,
            http_status=exc.http_status,
            retryable=exc.code == "admission_denied",
        )
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.code in ("execution_failed",),
                "details": exc.details,
                "trace_id": None,
            }
        },
    )


def not_found_response(exc: NotFound) -> JSONResponse:
    return ce_error_response(
        ChatEngineErrorCode.NOT_FOUND,
        f"{exc.kind} not found.",
        details={"kind": exc.kind, "id": exc.ident},
    )


def _json(model: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content=model.model_dump(mode="json", exclude_none=True)
    )


async def guarded(fn: Callable[[], Awaitable[Response]]) -> Response:
    try:
        return await fn()
    except NotFound as exc:
        return not_found_response(exc)
    except ChatError as exc:
        return chat_error_response(exc)


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["conversations"])
    svc: ChatService = rt.extensions["chat"]

    # -- projects --------------------------------------------------------------------------

    @router.post("/projects", status_code=201)
    async def create_project(body: ProjectCreate) -> Response:
        return _json(svc.create_project(body), 201)

    @router.get("/projects")
    async def list_projects() -> Response:
        items = svc.list_projects()
        return _json(ProjectsPage(projects=items, total=len(items)))

    @router.get("/projects/{project_id}")
    async def get_project(project_id: UUID) -> Response:
        async def run() -> Response:
            return _json(svc.get_project(project_id))

        return await guarded(run)

    @router.patch("/projects/{project_id}")
    async def update_project(project_id: UUID, body: ProjectUpdate) -> Response:
        async def run() -> Response:
            return _json(svc.update_project(project_id, body))

        return await guarded(run)

    @router.delete("/projects/{project_id}", status_code=204)
    async def delete_project(project_id: UUID) -> Response:
        async def run() -> Response:
            svc.delete_project(project_id)
            return Response(status_code=204)

        return await guarded(run)

    # -- conversations ---------------------------------------------------------------------

    @router.post("/conversations", status_code=201)
    async def create_conversation(body: ConversationCreate) -> Response:
        async def run() -> Response:
            return _json(svc.create_conversation(body), 201)

        return await guarded(run)

    @router.get("/conversations")
    async def list_conversations(
        project_id: UUID | None = Query(default=None),
        include_archived: bool = Query(default=False),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> Response:
        items = svc.list_conversations(project_id=project_id, include_archived=include_archived)
        return _json(
            ConversationsPage(conversations=items[offset : offset + limit], total=len(items))
        )

    @router.get("/conversations/{conversation_id}")
    async def get_conversation(conversation_id: UUID) -> Response:
        async def run() -> Response:
            return _json(svc.get_conversation(conversation_id))

        return await guarded(run)

    @router.patch("/conversations/{conversation_id}")
    async def update_conversation(conversation_id: UUID, body: ConversationUpdate) -> Response:
        async def run() -> Response:
            return _json(svc.update_conversation(conversation_id, body))

        return await guarded(run)

    @router.delete("/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(conversation_id: UUID) -> Response:
        async def run() -> Response:
            svc.delete_conversation(conversation_id)
            return Response(status_code=204)

        return await guarded(run)

    @router.get("/conversations/{conversation_id}/turns")
    async def list_turns(
        conversation_id: UUID,
        after_seq: int | None = Query(default=None, ge=0),
        limit: int | None = Query(default=None, ge=1, le=1000),
    ) -> Response:
        async def run() -> Response:
            all_turns = svc.turns(conversation_id)
            page = svc.turns(conversation_id, after_seq=after_seq, limit=limit)
            return _json(
                TurnsPage(turns=page, total=len(all_turns), conversation_id=conversation_id)
            )

        return await guarded(run)

    # -- chat (sync) -----------------------------------------------------------------------

    @router.post("/chat")
    async def chat(body: ChatRequest) -> Response:
        async def run() -> Response:
            return _json(await svc.chat(body))

        return await guarded(run)

    return router


__all__ = ["build_router", "chat_error_response", "not_found_response"]
