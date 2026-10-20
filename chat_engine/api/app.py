"""FastAPI app factory for chat_engine (D3: standalone service)."""

from __future__ import annotations

from collections.abc import Mapping

from core.contracts.errors import ErrorCode
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from chat_engine import UPSTREAM_SHA, __version__
from chat_engine.api import (
    admin_capabilities,
    admin_providers,
    anthropic_compat,
    apps,
    auth,
    catalog,
    chat_stream,
    conversations,
    embeddings,
    events,
    execute,
    models,
    openai_compat,
    skills,
    usage,
)
from chat_engine.api.errors import error_response
from chat_engine.api.openapi import install_openapi
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
    auth.install_auth(app, runtime)  # CE-24/D: no-op when CE_AUTH_MODE=off

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [str(err.get("msg", "")) for err in exc.errors()]
        if request.url.path.startswith("/v1/messages"):  # CE-24/I: Anthropic error envelope
            return anthropic_compat.anthropic_error(
                422, "Request body failed contract validation.", details={"errors": errors}
            )
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            "Request body failed contract validation.",
            details={"errors": errors},
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
            "gateway": runtime.extensions.get("gateway_report", {"bound": [], "skipped": {}}),
            "admission_backend": backend,
            "auth_mode": getattr(runtime.extensions.get("keys"), "mode", "off").__str__(),
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
            oai_router = openai_compat.build_router(runtime)
            app.include_router(oai_router)
            # CE-24/I: Anthropic Messages dialect — a mapper over the same handler
            if (environ or {}).get("CE_FEATURE_ANTHROPIC_COMPAT", "1") != "0":
                app.include_router(anthropic_compat.build_router(runtime, oai_router))
    if "skills" in runtime.extensions and "chat" in runtime.extensions:
        app.include_router(skills.build_router(runtime))
    if (
        "app_factory" in runtime.extensions
        and (environ or {}).get("CE_FEATURE_APP_FACTORY", "1") != "0"
    ):
        app.include_router(apps.build_router(runtime))
    if "usage_recorder" in runtime.extensions:
        app.include_router(usage.build_router(runtime))
    keys = runtime.extensions.get("keys")
    if keys is not None and keys.mode.value != "off":
        app.include_router(auth.build_router(runtime))
    # CE-24/H: embeddings over gateway create_embeddings routes (always mounted; answers
    # unsupported_capability when no embedding route is bound)
    app.include_router(embeddings.build_router(runtime))
    install_openapi(app, upstream_sha=UPSTREAM_SHA)
    mount_ui(app, environ)
    return app


def mount_ui(app: FastAPI, environ: Mapping[str, str] | None) -> None:
    """Serve the built standalone UI (``ui_standalone/dist``) at ``/`` when present.

    Resolution: ``CE_UI_DIST`` env → repo-relative ``ui_standalone/dist``. Absent ⇒ no
    static mount (API-only process). Registered LAST so ``/v1/*`` always wins.
    """
    import os
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    env = environ if environ is not None else os.environ
    candidate = env.get("CE_UI_DIST") or str(
        Path(__file__).resolve().parents[2] / "ui_standalone" / "dist"
    )
    dist = Path(candidate)
    if (dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="ui")
