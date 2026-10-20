"""POST /v1/embeddings (CE-24/H) — OpenAI-shaped embeddings over the gateway ``create_embeddings``
operation (e.g. ``gw_voyage``), governed like every other surface: admission (tenant /
provider / key scopes), usage record, pricing (litellm table prices voyage-* models),
unified error envelope.

Request  : ``{"model": "voyage-3", "input": "text" | ["text", ...], "provider"?: "gw_voyage"}``
Response : ``{"object": "list", "data": [{"object": "embedding", "index": i, "embedding": [...]}],
              "model": ..., "provider_key": ..., "dimensions": n, "usage": {...}}``
GET /v1/embeddings/models lists the embedding routes bound at boot.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from core.contracts.base import BoundedStr, ContractModel, JsonObject
from core.contracts.errors import ErrorCode
from core.contracts.model_policy import ExplicitModelPolicy, FallbackScope
from core.contracts.provider import ProviderOperation
from core.contracts.routing import RoutingRequest
from core.routing.router import NoEligibleCandidates
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field

from chat_engine.admission.port import rotate_on_failure
from chat_engine.api.errors import (
    ChatEngineErrorCode,
    ce_error_response,
    error_response,
    execution_failure_detail,
)
from chat_engine.auth.context import extra_scopes
from chat_engine.composition.runtime import Runtime

MAX_INPUTS = 128
MAX_CHARS = 32_000


class EmbeddingsRequest(ContractModel):
    model: BoundedStr
    input: str | list[str]
    provider: BoundedStr | None = None
    user: BoundedStr | None = None  # OpenAI compat, ignored
    encoding_format: BoundedStr | None = None  # OpenAI compat; only "float" supported
    dimensions: int | None = Field(default=None, ge=1)  # accepted, passed through if known


def _inputs(body: EmbeddingsRequest) -> list[str]:
    raw = [body.input] if isinstance(body.input, str) else list(body.input)
    return [s for s in (str(x) for x in raw)]


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(tags=["embeddings"])

    @router.get("/v1/embeddings/models")
    async def embedding_models() -> Response:
        routes: dict[str, Any] = rt.extensions.get("embedding_routes", {})
        data = [
            {"id": m, "object": "model", "owned_by": pk, "provider_key": pk}
            for pk, info in sorted(routes.items())
            for m in info.get("models", [])
        ]
        return JSONResponse(content={"object": "list", "data": data})

    @router.post("/v1/embeddings")
    async def embeddings(request: Request, body: EmbeddingsRequest) -> Response:
        inputs = _inputs(body)
        if not inputs or any(not s for s in inputs):
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "input must be a non-empty string or a list of non-empty strings.",
                details={"field": "input"},
            )
        if len(inputs) > MAX_INPUTS or sum(len(s) for s in inputs) > MAX_CHARS:
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                f"input exceeds limits ({MAX_INPUTS} items / {MAX_CHARS} chars).",
                details={"field": "input", "items": len(inputs)},
            )
        if body.encoding_format not in (None, "float"):
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "only encoding_format='float' is supported.",
                details={"field": "encoding_format"},
            )
        routes: dict[str, Any] = rt.extensions.get("embedding_routes", {})
        if not routes:
            return ce_error_response(
                ChatEngineErrorCode.UNSUPPORTED_CAPABILITY,
                "No embedding provider is bound (gateway route with create_embeddings).",
                details={"capability": "embeddings"},
            )
        policy = ExplicitModelPolicy(
            type="explicit_model",
            model_id=body.model,
            provider_id=body.provider,
            allow_fallback=body.provider is None,
            fallback_scope=FallbackScope.SAME_MODEL_DIFFERENT_PROVIDER
            if body.provider is None
            else None,
        )
        try:
            decision = rt.router.route(
                RoutingRequest(
                    operation=ProviderOperation.CREATE_EMBEDDINGS,
                    model_policy=policy,
                    required_capabilities=[],
                )
            )
        except NoEligibleCandidates as exc:
            return error_response(
                ErrorCode.MODEL_UNAVAILABLE,
                f"No embedding provider serves model '{body.model}'.",
                details={
                    "model": body.model,
                    "excluded": [
                        r.model_dump(mode="json", exclude_none=True) for r in exc.excluded
                    ],
                    "available": sorted(
                        {m for info in routes.values() for m in info.get("models", [])}
                    ),
                },
            )
        index = rt.extensions["catalog_index"]
        pk = index.provider_key(decision.selected.provider_id) or "?"
        admission = rt.extensions.get("admission")
        reservation = None
        est = sum(len(s) for s in inputs) // 4
        if admission is not None:
            from chat_engine.admission import Denied

            got = await admission.reserve(
                [f"tenant:{rt.tenant_id}", f"provider:{pk}", *extra_scopes()],
                tokens_estimate=est,
                units=1.0,
            )
            if isinstance(got, Denied):
                return ce_error_response(
                    ChatEngineErrorCode.ADMISSION_DENIED,
                    "Admission denied for this request.",
                    retryable=True,
                    details={
                        "scope": got.scope,
                        "dimension": got.dimension.value,
                        "retry_after_ms": got.retry_after_ms,
                    },
                )
            reservation = got
        # gateway CONTRACT §5: payload = {"inputs": [...]} (nothing else)
        payload: JsonObject = {"inputs": inputs}
        request_id = f"emb_{uuid4().hex[:24]}"
        try:
            report = await rt.execution.execute_single(
                tenant_id=rt.tenant_id,
                user_id=rt.user_id,
                decision=decision,
                operation=ProviderOperation.CREATE_EMBEDDINGS,
                payload=payload,
                request_hash=request_id,
            )
        except Exception:
            if admission is not None and reservation is not None:
                await admission.release(reservation)
            raise
        node = report.nodes[-1] if report.nodes else None
        resp = node.response if node else None
        usage = dict(resp.usage) if resp else {}
        ok = report.execution.status.value == "succeeded" and resp is not None and resp.succeeded
        if admission is not None and reservation is not None:
            total = usage.get("input_tokens", usage.get("total_tokens"))
            await admission.settle(
                reservation,
                tokens_actual=total if isinstance(total, int) else None,
                success=ok,
                category=rotate_on_failure(rt, pk, report),
            )
        rt.executions.put(report)
        winning = next((a for a in reversed(node.attempts) if a.succeeded), None) if node else None
        if winning is not None:
            pk = index.provider_key(winning.candidate.provider_id) or pk
        recorder = rt.extensions.get("usage_recorder")
        if recorder is not None:
            recorder.record(
                request_id=request_id,
                tenant_id=rt.tenant_id,
                surface="embeddings",
                status="succeeded" if ok else "failed",
                provider_key=pk,
                model_key=body.model,
                usage={
                    "input_tokens": usage.get("input_tokens"),
                    "total_tokens": usage.get("input_tokens", usage.get("total_tokens")),
                },
                latency_ms=resp.latency_ms if resp else None,
                fallback_chars=sum(len(s) for s in inputs),
                metadata={"items": len(inputs)},
            )
        if not ok or resp is None:
            err = resp.error if resp else None
            detail = execution_failure_detail(str(report.execution.id), err, stage=None)
            from chat_engine.api.errors import HTTP_STATUS_BY_CODE

            return JSONResponse(
                status_code=HTTP_STATUS_BY_CODE[detail.code],
                content={"error": detail.model_dump(mode="json", exclude_none=True)},
            )
        vectors = resp.output.get("embeddings")
        if not isinstance(vectors, list):
            return error_response(
                ErrorCode.EXECUTION_FAILED,
                "Provider returned no embeddings.",
                details={"execution_id": str(report.execution.id)},
            )
        dims = resp.output.get("dimensions")
        if not isinstance(dims, int) and vectors and isinstance(vectors[0], list):
            dims = len(vectors[0])
        return JSONResponse(
            content={
                "object": "list",
                "data": [
                    {"object": "embedding", "index": i, "embedding": v}
                    for i, v in enumerate(vectors)
                ],
                "model": body.model,
                "provider_key": pk,
                "dimensions": dims,
                "execution_id": str(report.execution.id),
                "usage": {
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "total_tokens": usage.get("input_tokens", 0),
                },
            }
        )

    return router


__all__ = ["EmbeddingsRequest", "build_router"]
