"""/v1/admin/capabilities/ledger (+probe/enable/disable) and /v1/admin/admission."""

from __future__ import annotations

from core.contracts.base import ContractModel
from fastapi import APIRouter, Body, Response
from fastapi.responses import JSONResponse

from chat_engine.api.errors import ChatEngineErrorCode, ce_error_response
from chat_engine.capabilities import ALL_CAPABILITIES, CapabilityLedger, LedgerListResponse
from chat_engine.composition.runtime import Runtime


class ProbeRequest(ContractModel):
    model_key: str | None = None


class EnableRequest(ContractModel):
    enabled: bool
    model_key: str | None = None
    policy_ref: str | None = None


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(tags=["admin"])
    ledger: CapabilityLedger = rt.extensions["capabilities"]

    @router.get("/v1/admin/capabilities/ledger")
    async def ledger_list(
        provider_key: str | None = None,
        capability: str | None = None,
        executable_only: bool = False,
    ) -> Response:
        rows = ledger.entries(
            provider_key=provider_key, capability=capability, executable_only=executable_only
        )
        return JSONResponse(
            content=LedgerListResponse(entries=rows, total=len(rows)).model_dump(
                mode="json", exclude_none=True
            )
        )

    @router.get("/v1/admin/capabilities/catalog")
    async def capability_catalog() -> Response:
        return JSONResponse(
            content={
                "capabilities": list(ALL_CAPABILITIES),
                "probes": [c for c in ALL_CAPABILITIES if ledger.has_probe(c)],
            }
        )

    @router.get("/v1/admin/capabilities/probes")
    async def probe_results(provider_key: str | None = None) -> Response:
        return JSONResponse(
            content={
                "results": [
                    r.model_dump(mode="json", exclude_none=True)
                    for r in ledger.results(provider_key=provider_key)
                ]
            }
        )

    @router.post("/v1/admin/capabilities/{provider_key}/{capability}:probe")
    async def probe(
        provider_key: str, capability: str, body: ProbeRequest | None = Body(default=None)
    ) -> Response:
        if provider_key not in rt.providers.all_keys():
            return ce_error_response(
                ChatEngineErrorCode.NOT_FOUND,
                "Unknown provider.",
                details={"provider_key": provider_key},
            )
        try:
            result = await ledger.probe(
                provider_key, capability, model_key=(body or ProbeRequest()).model_key
            )
        except LookupError as exc:
            return ce_error_response(
                ChatEngineErrorCode.NOT_FOUND, str(exc), details={"capability": capability}
            )
        return JSONResponse(content=result.model_dump(mode="json", exclude_none=True))

    @router.post("/v1/admin/capabilities/{provider_key}/{capability}:enable")
    async def enable(provider_key: str, capability: str, body: EnableRequest) -> Response:
        try:
            entry = ledger.set_enabled(
                provider_key,
                capability,
                body.enabled,
                model_key=body.model_key,
                policy_ref=body.policy_ref,
            )
        except LookupError:
            return ce_error_response(
                ChatEngineErrorCode.NOT_FOUND,
                "Capability is not declared for this provider.",
                details={"provider_key": provider_key, "capability": capability},
            )
        return JSONResponse(content=entry.model_dump(mode="json", exclude_none=True))

    @router.get("/v1/admin/admission")
    async def admission_snapshot() -> Response:
        adm = rt.extensions["admission"]
        snap = await adm.snapshot_async() if hasattr(adm, "snapshot_async") else adm.snapshot()
        return JSONResponse(
            content={
                "backend": adm.backend_name,
                "replicas_safe": adm.backend_name != "in_memory",
                # CE-24/B: effective breaker policy (legacy relative breaker when no rules)
                "breaker_policy": pol.describe() if (pol := getattr(adm, "policy", None)) else None,
                "scopes": snap,
            }
        )

    @router.get("/v1/admin/routing/signals")
    async def routing_signals() -> Response:
        """CE-24/C: live latency / in-flight per provider (feeds optional reordering)."""
        live = rt.extensions.get("live_signals")
        return JSONResponse(
            content={
                "enabled": bool(getattr(live, "enabled", False)),
                "providers": live.snapshot() if live is not None else [],
            }
        )

    return router
