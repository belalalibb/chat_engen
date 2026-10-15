"""/v1/catalog/* — unified catalog (one row per canonical model) + alias admin."""

from __future__ import annotations

from fastapi import APIRouter, Query, Response
from fastapi.responses import JSONResponse

from chat_engine.api.errors import ChatEngineErrorCode, ce_error_response
from chat_engine.catalog import AliasDecision, AliasNotFound, CatalogService
from chat_engine.catalog.contracts import AliasDecisionRequest, AliasListResponse
from chat_engine.composition.runtime import Runtime


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/v1/catalog", tags=["catalog"])
    svc: CatalogService = rt.extensions["catalog"]

    @router.get("/models")
    async def list_catalog(
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=500),
        q: str | None = None,
        include_providers: bool = True,
    ) -> Response:
        page = svc.page(offset=offset, limit=limit, q=q, include_providers=include_providers)
        return JSONResponse(content=page.model_dump(mode="json", exclude_none=True))

    @router.get("/models/{canonical_id:path}")
    async def get_canonical(canonical_id: str) -> Response:
        for entry in svc.entries():
            if entry.canonical.canonical_id == canonical_id:
                return JSONResponse(content=entry.model_dump(mode="json", exclude_none=True))
        return ce_error_response(ChatEngineErrorCode.NOT_FOUND, "Unknown canonical model.", details={"canonical_id": canonical_id})

    @router.get("/aliases")
    async def list_aliases(decision: AliasDecision | None = None, alias: str | None = None) -> Response:
        rows = svc.list_aliases(decision=decision, alias=alias)
        return JSONResponse(content=AliasListResponse(aliases=rows, total=len(rows)).model_dump(mode="json", exclude_none=True))

    @router.post("/aliases/{alias:path}:decide")
    async def decide_alias(alias: str, body: AliasDecisionRequest) -> Response:
        if body.canonical_id is None:
            return ce_error_response(ChatEngineErrorCode.CONFLICT, "canonical_id is required.", details={"field": "canonical_id"}, http_status=422)
        try:
            row = svc.decide(alias, body.canonical_id, body.decision, reason=body.reason, decided_by=body.decided_by)
        except AliasNotFound:
            return ce_error_response(ChatEngineErrorCode.NOT_FOUND, "Unknown model_key alias.", details={"alias": alias})
        return JSONResponse(content=row.model_dump(mode="json", exclude_none=True))

    return router
