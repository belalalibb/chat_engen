"""/v1/admin/providers/* (lifecycle D2) + /v1/credentials (BYOK) + upstream-shaped
read routes /v1/admin/providers, /v1/admin/models, /v1/admin/routing/weights."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Body, Response
from fastapi.responses import JSONResponse

from chat_engine.api.errors import ChatEngineErrorCode, ce_error_response
from chat_engine.composition.runtime import Runtime
from chat_engine.credentials import (
    CredentialCreateRequest,
    CredentialListResponse,
    CredentialNotFound,
    CredentialService,
)
from chat_engine.providers.lifecycle import (
    InvalidTransition,
    ProviderConflict,
    ProviderDraftRequest,
    ProviderLifecycleService,
    RollbackRequest,
    TransitionRequest,
    UnknownProvider,
)


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(tags=["admin"])
    lifecycle: ProviderLifecycleService = rt.extensions["lifecycle"]
    credentials: CredentialService = rt.extensions["credentials"]

    def _lifecycle_error(exc: Exception) -> JSONResponse:
        if isinstance(exc, UnknownProvider):
            return ce_error_response(
                ChatEngineErrorCode.NOT_FOUND,
                "Unknown managed provider.",
                details={"detail": str(exc)},
            )
        if isinstance(exc, InvalidTransition):
            return ce_error_response(
                ChatEngineErrorCode.CONFLICT,
                str(exc),
                details={"current": exc.current.value, "target": exc.target.value},
            )
        if isinstance(exc, ProviderConflict):
            return ce_error_response(ChatEngineErrorCode.CONFLICT, str(exc))
        raise exc

    # -- upstream-shaped reads (COMPATIBILITY_MAP §3) -----------------------------------

    @router.get("/v1/admin/providers")
    async def list_all_providers() -> Response:
        managed = {s.provider_key: s for s in lifecycle.list_all()}
        rows = []
        for key in sorted(set(rt.providers.all_keys()) | set(managed)):
            summary = managed.get(key)
            registered = key in rt.providers.all_keys()
            manifest = rt.providers.get(key).manifest if registered else None
            rows.append(
                {
                    "provider_key": key,
                    "display_name": summary.display_name
                    if summary
                    else (manifest.name if manifest else key),
                    "status": "active"
                    if registered
                    else (summary.state.value if summary else "unknown"),
                    "managed": summary is not None,
                    "lifecycle_state": summary.state.value if summary else None,
                    "revision": summary.revision if summary else None,
                    "operations": [o.value for o in manifest.operations] if manifest else [],
                    "is_template": bool(manifest and manifest.is_template),
                    "model_count": len(
                        rt.bindings.bindings_for_provider(rt.providers.get(key).provider.id)
                    )
                    if registered
                    else (summary.model_count if summary else 0),
                }
            )
        return JSONResponse(content={"providers": rows})

    @router.get("/v1/admin/models")
    async def list_all_models() -> Response:
        index = rt.extensions["catalog_index"]
        rows = [
            {
                "model_key": m.model_key,
                "display_name": m.display_name,
                "tier": m.tier.value,
                "status": m.status.value,
                "providers": index.provider_keys_for_model(m.id),
            }
            for m in rt.models.all_models()
        ]
        return JSONResponse(content={"models": sorted(rows, key=lambda r: r["model_key"])})

    @router.get("/v1/admin/routing/weights")
    async def routing_weights() -> Response:
        return JSONResponse(content=rt.router.default_weights.model_dump(mode="json"))

    # -- lifecycle -----------------------------------------------------------------------

    @router.get("/v1/admin/providers/managed")
    async def list_managed() -> Response:
        return JSONResponse(
            content={"providers": [s.model_dump(mode="json") for s in lifecycle.list_all()]}
        )

    @router.post("/v1/admin/providers/{provider_key}:validate")
    async def validate(
        provider_key: str, body: TransitionRequest | None = Body(default=None)
    ) -> Response:
        try:
            rev = await lifecycle.validate(
                provider_key, changed_by=(body or TransitionRequest()).changed_by
            )
        except Exception as exc:
            return _lifecycle_error(exc)
        return JSONResponse(content=rev.model_dump(mode="json", exclude_none=True))

    @router.post("/v1/admin/providers/{provider_key}:verify")
    async def verify(
        provider_key: str, body: TransitionRequest | None = Body(default=None)
    ) -> Response:
        try:
            rev = await lifecycle.verify(
                provider_key, changed_by=(body or TransitionRequest()).changed_by
            )
        except Exception as exc:
            return _lifecycle_error(exc)
        return JSONResponse(content=rev.model_dump(mode="json", exclude_none=True))

    @router.post("/v1/admin/providers/{provider_key}:enable")
    async def enable(
        provider_key: str, body: TransitionRequest | None = Body(default=None)
    ) -> Response:
        try:
            rev = lifecycle.enable(
                provider_key, changed_by=(body or TransitionRequest()).changed_by
            )
        except Exception as exc:
            return _lifecycle_error(exc)
        return JSONResponse(content=rev.model_dump(mode="json", exclude_none=True))

    @router.post("/v1/admin/providers/{provider_key}:disable")
    async def disable(
        provider_key: str, body: TransitionRequest | None = Body(default=None)
    ) -> Response:
        b = body or TransitionRequest()
        try:
            rev = lifecycle.disable(provider_key, changed_by=b.changed_by, reason=b.reason)
        except Exception as exc:
            return _lifecycle_error(exc)
        return JSONResponse(content=rev.model_dump(mode="json", exclude_none=True))

    @router.post("/v1/admin/providers/{provider_key}:archive")
    async def archive(
        provider_key: str, body: TransitionRequest | None = Body(default=None)
    ) -> Response:
        b = body or TransitionRequest()
        try:
            rev = lifecycle.archive(provider_key, changed_by=b.changed_by, reason=b.reason)
        except Exception as exc:
            return _lifecycle_error(exc)
        return JSONResponse(content=rev.model_dump(mode="json", exclude_none=True))

    @router.post("/v1/admin/providers/{provider_key}:rollback")
    async def rollback(provider_key: str, body: RollbackRequest) -> Response:
        try:
            rev = lifecycle.rollback(
                provider_key, body.revision, changed_by=body.changed_by, reason=body.reason
            )
        except Exception as exc:
            return _lifecycle_error(exc)
        return JSONResponse(content=rev.model_dump(mode="json", exclude_none=True))

    # -- generic {provider_key} routes LAST (':action' literals must win the match) --

    @router.get("/v1/admin/providers/{provider_key}/revisions")
    async def revisions(provider_key: str) -> Response:
        try:
            return JSONResponse(
                content={
                    "revisions": [
                        r.model_dump(mode="json", exclude_none=True)
                        for r in lifecycle.revisions(provider_key)
                    ]
                }
            )
        except Exception as exc:
            return _lifecycle_error(exc)

    @router.get("/v1/admin/providers/{provider_key}/credentials")
    async def provider_credentials(provider_key: str) -> Response:
        """CE-24/K: credential pool state (labels only — never the secrets)."""
        pool = rt.extensions.get("credential_pool")
        if provider_key not in rt.provider_keys:
            return ce_error_response(
                ChatEngineErrorCode.NOT_FOUND,
                "Provider not found.",
                details={"provider_key": provider_key},
            )
        if pool is not None:
            pool.unpark_expired()
        return JSONResponse(
            content=pool.describe(provider_key)
            if pool is not None
            else {"provider_key": provider_key, "accounts": [], "active": None, "rotations": 0}
        )

    @router.get("/v1/admin/providers/{provider_key}")
    async def get_managed(provider_key: str) -> Response:
        try:
            return JSONResponse(content=lifecycle.get(provider_key).model_dump(mode="json"))
        except Exception as exc:
            return _lifecycle_error(exc)

    @router.post("/v1/admin/providers/{provider_key}", status_code=201)
    async def create_draft(provider_key: str, body: ProviderDraftRequest) -> Response:
        try:
            rev = lifecycle.draft(provider_key, body)
        except Exception as exc:
            return _lifecycle_error(exc)
        return JSONResponse(status_code=201, content=rev.model_dump(mode="json", exclude_none=True))

    @router.put("/v1/admin/providers/{provider_key}")
    async def update_draft(provider_key: str, body: ProviderDraftRequest) -> Response:
        try:
            rev = lifecycle.update_draft(provider_key, body)
        except Exception as exc:
            return _lifecycle_error(exc)
        return JSONResponse(content=rev.model_dump(mode="json", exclude_none=True))

    # -- BYOK credentials ------------------------------------------------------------------

    @router.post("/v1/credentials", status_code=201)
    async def create_credential(body: CredentialCreateRequest) -> Response:
        row = credentials.create(body)
        return JSONResponse(status_code=201, content=row.model_dump(mode="json", exclude_none=True))

    @router.get("/v1/credentials")
    async def list_credentials() -> Response:
        return JSONResponse(
            content=CredentialListResponse(credentials=credentials.list()).model_dump(
                mode="json", exclude_none=True
            )
        )

    @router.delete("/v1/credentials/{credential_id}")
    async def revoke_credential(credential_id: str) -> Response:
        try:
            row = credentials.revoke(UUID(credential_id))
        except (ValueError, CredentialNotFound):
            return ce_error_response(
                ChatEngineErrorCode.NOT_FOUND,
                "Unknown credential.",
                details={"credential_id": credential_id},
            )
        return JSONResponse(content=row.model_dump(mode="json", exclude_none=True))

    return router
