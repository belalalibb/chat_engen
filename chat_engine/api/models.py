"""``GET /v1/models`` — upstream-compatible (ModelListEntry rows, runtime truth)."""

from __future__ import annotations

from uuid import UUID

from core.contracts.base import JsonObject
from core.contracts.domain import BindingAvailability
from core.contracts.model_listing import ModelListEntry, ModelsListResponse
from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from chat_engine.composition.runtime import Runtime


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    def _provider_key(provider_id: UUID) -> str | None:
        try:
            return rt.providers.get_by_id(provider_id).provider.provider_key
        except Exception:  # noqa: BLE001
            return None

    @router.get("/v1/models")
    async def list_models() -> Response:
        rows: list[ModelListEntry] = []
        board = rt.router.signals
        for model in rt.models.active_models():
            bindings_for = rt.bindings.bindings_for_model(model.id)
            entry = ModelListEntry.from_model(model, bindings_for, provider_keys=_provider_key)
            if board is not None and bindings_for:
                runtime_rows: list[JsonObject] = []
                blocked = 0
                for binding in bindings_for:
                    elig = board.eligibility(binding.provider_id, model.id, limits=binding.limits_metadata)
                    if not elig.eligible:
                        blocked += 1
                    runtime_rows.append(
                        {
                            "provider": _provider_key(binding.provider_id),
                            "binding_availability": binding.availability.value,
                            "eligible": elig.eligible,
                            "reason": elig.reason,
                            "retry_after_ms": elig.retry_after_ms,
                        }
                    )
                availability = entry.availability
                if blocked == len(bindings_for):
                    availability = BindingAvailability.UNAVAILABLE
                elif blocked and availability is BindingAvailability.AVAILABLE:
                    availability = BindingAvailability.DEGRADED
                entry = entry.model_copy(update={"availability": availability, "runtime": runtime_rows})
            rows.append(entry)
        return JSONResponse(status_code=200, content=ModelsListResponse(models=rows).model_dump(mode="json", exclude_none=True))

    return router
