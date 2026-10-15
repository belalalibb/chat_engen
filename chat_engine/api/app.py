"""FastAPI app factory for chat_engine (D3: standalone service)."""

from __future__ import annotations

from collections.abc import Mapping

from core.contracts.errors import ErrorCode
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from chat_engine import __version__
from chat_engine.api import (
    admin_capabilities,
    admin_providers,
    catalog,
    chat_stream,
    conversations,
    events,
    execute,
    models,
    openai_compat,
)
from chat_engine.api.errors import error_response
from chat_engine.composition.runtime import Runtime, build_runtime
from chat_engine.util.time import utc_now

#: Upstream /v1 routes chat_engine deliberately does NOT serve (COMPATIBILITY_MAP §3).
NOT_SERVED_UPSTREAM_ROUTES: tuple[str, ...] = (
    "/v1/webhooks",
    "/v1/workspaces",
    "/v1/admin/capabilities",
    "/v1/admin/changes",
    "/v1/admin/evaluations",
    "/v1/admin/learning",
    "/v1/admin/plans",
    "/v1/admin/scenarios",
    "/v1/admin/self-review",
    "/v1/admin/source-changes",
)


def create_app(rt: Runtime | None = None, *, environ: Mapping[str, str] | None = None) -> FastAPI:
    runtime = rt if rt is not None else build_runtime(environ)
    app = FastAPI(
        title="chat_engine", version=__version__, docs_url="/docs", openapi_url="/openapi.json"
    )
    app.state.runtime = runtime

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            "Request body failed contract validation.",
            details={"errors": [str(err.get("msg", "")) for err in exc.errors()]},
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        admission = runtime.extensions.get("admission")
        backend = getattr(admission, "backend_name", "in_memory")
        return {
            "status": "alive",
            "scope": "process",
            "time": utc_now().isoformat(),
            "service": "chat_engine",
            "version": __version__,
            "upstream_sha": runtime.upstream_sha,
            "profile": runtime.profile,
            "providers": list(runtime.provider_keys),
            "admission_backend": backend,
            "replicas_safe": backend != "in_memory",
            "compat": {"not_served": list(NOT_SERVED_UPSTREAM_ROUTES)},
        }

    app.include_router(execute.build_router(runtime))
    app.include_router(models.build_router(runtime))
    app.include_router(events.build_router(runtime))
    # Feature routers — each checks the composition slot it needs.
    if "catalog" in runtime.extensions:
        app.include_router(catalog.build_router(runtime))
    if "lifecycle" in runtime.extensions:
        app.include_router(admin_providers.build_router(runtime))
    if "capabilities" in runtime.extensions:
        app.include_router(admin_capabilities.build_router(runtime))
    if "streaming" in runtime.extensions:
        app.include_router(chat_stream.build_router(runtime))
    if "chat" in runtime.extensions:
        app.include_router(conversations.build_router(runtime))
        if (environ or {}).get("CE_FEATURE_OPENAI_COMPAT", "1") != "0":
            app.include_router(openai_compat.build_router(runtime))
    return app
