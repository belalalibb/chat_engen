"""Virtual keys API + Bearer middleware (CE-24/D).

Routes (served only when ``CE_AUTH_MODE`` != ``off``):
    POST   /v1/admin/keys                 master only → KeyCreated (secret shown once)
    GET    /v1/admin/keys                 master only
    GET    /v1/admin/keys/{key_id}        master, or the key itself
    PATCH  /v1/admin/keys/{key_id}        master only
    DELETE /v1/admin/keys/{key_id}        master only
    GET    /v1/auth/whoami                any resolved principal

Middleware resolves ``Authorization: Bearer ce_live_…`` into ``request.state.principal``.
``required`` mode rejects anonymous requests on PROTECTED_PREFIXES with 401
``unauthenticated``; ``/healthz``, ``/docs``, ``/openapi.json`` and the UI stay open.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from core.contracts.errors import ErrorCode
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from chat_engine.api.errors import error_response
from chat_engine.auth import AuthMode, KeyCreate, KeyService, KeyUpdate, Principal
from chat_engine.auth.context import CURRENT_PRINCIPAL
from chat_engine.auth.service import KeyNotFound, KeyRejected
from chat_engine.composition.runtime import Runtime

PROTECTED_PREFIXES = ("/v1/",)
OPEN_PATHS = ("/healthz", "/docs", "/openapi.json", "/redoc", "/v1/describe")


def _json(model: object, status: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=model.model_dump(mode="json", exclude_none=True),  # type: ignore[attr-defined]
    )


def principal_of(request: Request) -> Principal | None:
    return getattr(request.state, "principal", None)


def _bearer(request: Request) -> str | None:
    raw = request.headers.get("authorization") or request.headers.get("x-api-key")
    if not raw:
        return None
    if raw.lower().startswith("bearer "):
        return raw[7:].strip() or None
    return raw.strip() or None


def install_auth(app: FastAPI, rt: Runtime) -> None:
    keys: KeyService | None = rt.extensions.get("keys")
    if keys is None or keys.mode is AuthMode.OFF:
        return

    @app.middleware("http")
    async def _auth_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        try:
            principal = keys.resolve(_bearer(request))
        except KeyNotFound:
            return error_response(
                ErrorCode.UNAUTHENTICATED, "Unknown API key.", details={"reason": "unknown_key"}
            )
        except KeyRejected as exc:
            code = (
                ErrorCode.ENTITLEMENT_EXCEEDED
                if exc.reason == "budget_exhausted"
                else (ErrorCode.UNAUTHORIZED)
            )
            return error_response(code, str(exc), details={"reason": exc.reason})
        if principal is None:
            if (
                keys.mode is AuthMode.REQUIRED
                and path.startswith(PROTECTED_PREFIXES)
                and not path.startswith(OPEN_PATHS)
            ):
                return error_response(
                    ErrorCode.UNAUTHENTICATED,
                    "This deployment requires an API key (Authorization: Bearer …).",
                    details={"reason": "missing_key", "auth_mode": keys.mode.value},
                )
            principal = keys.anonymous()
        request.state.principal = principal
        token = CURRENT_PRINCIPAL.set(principal)
        try:
            return await call_next(request)
        finally:
            CURRENT_PRINCIPAL.reset(token)


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(tags=["auth"])
    keys: KeyService = rt.extensions["keys"]

    def _require_master(request: Request) -> JSONResponse | None:
        p = principal_of(request)
        if p is None or p.anonymous:
            return error_response(
                ErrorCode.UNAUTHENTICATED,
                "Key management requires the master key.",
                details={"reason": "missing_key"},
            )
        if not p.is_master:
            return error_response(
                ErrorCode.UNAUTHORIZED,
                "Key management requires the master key.",
                details={"reason": "master_required"},
            )
        return None

    @router.get("/v1/auth/whoami")
    async def whoami(request: Request) -> Response:
        p = principal_of(request) or keys.anonymous()
        body = p.model_dump(mode="json", exclude_none=True)
        body["auth_mode"] = keys.mode.value
        return JSONResponse(content=body)

    @router.post("/v1/admin/keys", status_code=201)
    async def create_key(request: Request, body: KeyCreate) -> Response:
        if (deny := _require_master(request)) is not None:
            return deny
        return _json(keys.create(body), 201)

    @router.get("/v1/admin/keys")
    async def list_keys(request: Request) -> Response:
        if (deny := _require_master(request)) is not None:
            return deny
        rows = keys.list()
        return JSONResponse(
            content={
                "auth_mode": keys.mode.value,
                "total": len(rows),
                "keys": [r.model_dump(mode="json", exclude_none=True) for r in rows],
            }
        )

    @router.get("/v1/admin/keys/{key_id}")
    async def get_key(request: Request, key_id: UUID) -> Response:
        p = principal_of(request)
        if p is None or p.anonymous:
            return error_response(
                ErrorCode.UNAUTHENTICATED, "API key required.", details={"reason": "missing_key"}
            )
        if not p.is_master and p.key_id != key_id:
            return error_response(
                ErrorCode.UNAUTHORIZED,
                "A key may only read its own record.",
                details={"reason": "master_required"},
            )
        try:
            return _json(keys.get(key_id))
        except KeyNotFound:
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "Key not found.",
                details={"key_id": str(key_id)},
                http_status=404,
            )

    @router.patch("/v1/admin/keys/{key_id}")
    async def update_key(request: Request, key_id: UUID, body: KeyUpdate) -> Response:
        if (deny := _require_master(request)) is not None:
            return deny
        try:
            return _json(keys.update(key_id, body))
        except KeyNotFound:
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "Key not found.",
                details={"key_id": str(key_id)},
                http_status=404,
            )

    @router.delete("/v1/admin/keys/{key_id}", status_code=204)
    async def delete_key(request: Request, key_id: UUID) -> Response:
        if (deny := _require_master(request)) is not None:
            return deny
        try:
            keys.delete(key_id)
        except KeyNotFound:
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "Key not found.",
                details={"key_id": str(key_id)},
                http_status=404,
            )
        except KeyRejected as exc:
            return error_response(ErrorCode.UNAUTHORIZED, str(exc), details={"reason": exc.reason})
        return Response(status_code=204)

    return router


__all__ = ["OPEN_PATHS", "PROTECTED_PREFIXES", "build_router", "install_auth", "principal_of"]
