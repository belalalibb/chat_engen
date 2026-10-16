"""CE-15 — GET /v1/usage (aggregates), GET /v1/usage/records, pricing admin."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Response
from fastapi.responses import JSONResponse

from chat_engine.composition.runtime import Runtime
from chat_engine.usage import PricingUpsert, UsageRecorder


def _json(model: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content=model.model_dump(mode="json", exclude_none=True)
    )


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(tags=["usage"])
    rec: UsageRecorder = rt.extensions["usage_recorder"]

    @router.get("/v1/usage")
    async def usage(
        group_by: str = Query(
            default="provider_key",
            description="comma-separated: tenant_id,project_id,conversation_id,provider_key,model_key,canonical_id,surface,status,day,strategy",
        ),
        since: datetime | None = None,
        until: datetime | None = None,
        project_id: str | None = None,
        conversation_id: str | None = None,
        provider_key: str | None = None,
        model_key: str | None = None,
        surface: str | None = None,
    ) -> Response:
        return _json(
            rec.report(
                group_by=[g.strip() for g in group_by.split(",") if g.strip()],
                since=since,
                until=until,
                project_id=project_id,
                conversation_id=conversation_id,
                provider_key=provider_key,
                model_key=model_key,
                surface=surface,
            )
        )

    @router.get("/v1/usage/records")
    async def records(
        limit: int = Query(default=100, ge=1, le=1000), offset: int = Query(default=0, ge=0)
    ) -> Response:
        rows = rec.all()
        return JSONResponse(
            content={
                "records": [
                    r.model_dump(mode="json", exclude_none=True)
                    for r in rows[offset : offset + limit]
                ],
                "total": len(rows),
            }
        )

    @router.get("/v1/usage/records/{request_id}")
    async def record(request_id: str) -> Response:
        r = rec.get(request_id)
        if r is None:
            return JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "code": "not_found",
                        "message": "no usage record",
                        "retryable": False,
                        "details": {"request_id": request_id},
                        "trace_id": None,
                    }
                },
            )
        return _json(r)

    @router.get("/v1/admin/pricing")
    async def pricing(model_key: str | None = None, provider_key: str | None = None) -> Response:
        if model_key:
            p = rec.pricing.lookup(provider_key, model_key)
            return JSONResponse(
                content={"pricing": p.model_dump(mode="json", exclude_none=True) if p else None}
            )
        rows = rec.pricing.records()  # triggers lazy snapshot load
        return JSONResponse(
            content={
                "snapshot_loaded": rec.pricing.snapshot_loaded,
                "snapshot_fetched_at": rec.pricing.snapshot_fetched_at,
                "total": len(rows),
                "pricing": [r.model_dump(mode="json", exclude_none=True) for r in rows[:500]],
            }
        )

    @router.put("/v1/admin/pricing")
    async def upsert_pricing(body: PricingUpsert) -> Response:
        return _json(rec.pricing.upsert(body), 201)

    return router


__all__ = ["build_router"]
