"""CE-14 — POST /v1/apps:generate (async), GET /v1/apps, GET /v1/apps/{id},
POST /v1/apps/{id}:approve, :cancel, GET /v1/apps/{id}/artifacts/{name}."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Response
from fastapi.responses import JSONResponse

from chat_engine.api.errors import ChatEngineErrorCode, ce_error_response
from chat_engine.app_factory import AppFactoryRunner, ApproveRequest, GenerateRequest, JobNotFound
from chat_engine.composition.runtime import Runtime
from chat_engine.conversations.service import ChatError


def _json(model: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=model.model_dump(mode="json", exclude_none=True))


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(tags=["apps"])
    runner: AppFactoryRunner = rt.extensions["app_factory"]

    @router.post("/v1/apps:generate", status_code=202)
    async def generate(body: GenerateRequest) -> Response:
        job = runner.submit(body)
        return JSONResponse(
            status_code=202,
            content={**job.model_dump(mode="json", exclude_none=True), "status_url": f"/v1/apps/{job.id}"},
        )

    @router.get("/v1/apps")
    async def list_jobs() -> Response:
        items = runner.list_jobs()
        return JSONResponse(content={"jobs": [j.model_dump(mode="json", exclude_none=True) for j in items], "total": len(items)})

    @router.get("/v1/apps/{job_id}")
    async def get_job(job_id: UUID) -> Response:
        try:
            return _json(runner.get(job_id))
        except JobNotFound:
            return ce_error_response(ChatEngineErrorCode.NOT_FOUND, "Unknown app job.", details={"job_id": str(job_id)})

    @router.post("/v1/apps/{job_id}:approve")
    async def approve(job_id: UUID, body: ApproveRequest | None = Body(default=None)) -> Response:
        try:
            return _json(runner.approve(job_id, approved_by=(body or ApproveRequest()).approved_by))
        except JobNotFound:
            return ce_error_response(ChatEngineErrorCode.NOT_FOUND, "Unknown app job.", details={"job_id": str(job_id)})
        except ChatError as exc:
            return ce_error_response(ChatEngineErrorCode.CONFLICT, exc.message, http_status=exc.http_status)

    @router.post("/v1/apps/{job_id}:cancel")
    async def cancel(job_id: UUID) -> Response:
        try:
            return _json(runner.cancel(job_id))
        except JobNotFound:
            return ce_error_response(ChatEngineErrorCode.NOT_FOUND, "Unknown app job.", details={"job_id": str(job_id)})

    @router.get("/v1/apps/{job_id}/artifacts/{name:path}")
    async def artifact(job_id: UUID, name: str) -> Response:
        try:
            job = runner.get(job_id)
        except JobNotFound:
            return ce_error_response(ChatEngineErrorCode.NOT_FOUND, "Unknown app job.", details={"job_id": str(job_id)})
        ref = next((a for a in job.artifacts if a.name == name), None)
        if ref is None:
            return ce_error_response(ChatEngineErrorCode.NOT_FOUND, "Unknown artifact.", details={"name": name})
        data = runner.artifact_store.get(ref.ref)
        return Response(content=data, media_type=ref.content_type, headers={"x-artifact-sha256": ref.sha256})

    return router


__all__ = ["build_router"]
