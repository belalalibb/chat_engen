"""AdmissionPort (ADR-CE-0001 D5): atomic reserve/settle/release over scopes×dims.

Scopes: ``provider``, ``provider:account``, ``provider:model``, ``tenant``,
``tenant:project``. Dimensions: rpm, tpm, concurrency, budget (task units).
Unknown limits are NOT infinite: the conservative default applies and is
labelled ``limits_source=conservative_default`` so operators can see it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from core.contracts.base import BoundedStr, ContractModel, JsonObject
from pydantic import Field

CONSERVATIVE_DEFAULT_LIMITS: dict[str, int] = {"rpm": 60, "concurrency": 4}
WINDOW_SECONDS = 60
DAY_SECONDS = 86_400  # CE-24/L: rolling 24h window for rpd/tpd


class Dim(StrEnum):
    RPM = "rpm"
    TPM = "tpm"
    CONCURRENCY = "concurrency"
    BUDGET = "budget"
    RPD = "rpd"  # CE-24/L requests per day (rolling 24h)
    TPD = "tpd"  # CE-24/L tokens per day (rolling 24h)


class Limits(ContractModel):
    rpm: int | None = Field(default=None, ge=1)
    tpm: int | None = Field(default=None, ge=1)
    concurrency: int | None = Field(default=None, ge=1)
    budget: float | None = Field(default=None, ge=0)
    rpd: int | None = Field(default=None, ge=1)  # CE-24/L, optional, default unlimited
    tpd: int | None = Field(default=None, ge=1)
    source: BoundedStr = "conservative_default"

    @classmethod
    def conservative(cls) -> Limits:
        return cls(
            rpm=CONSERVATIVE_DEFAULT_LIMITS["rpm"],
            concurrency=CONSERVATIVE_DEFAULT_LIMITS["concurrency"],
            source="conservative_default",
        )

    @classmethod
    def from_metadata(cls, meta: JsonObject | None, *, source: str = "provider_metadata") -> Limits:
        """Read upstream ``limits_metadata`` ({rpm|requests_per_minute, tpm|tokens_per_minute, concurrency})."""
        meta = meta or {}
        rpm = _int(meta.get("rpm", meta.get("requests_per_minute")))
        tpm = _int(meta.get("tpm", meta.get("tokens_per_minute")))
        conc = _int(meta.get("concurrency", meta.get("max_concurrency")))
        if rpm is None and tpm is None and conc is None:
            return cls.conservative()
        return cls(
            rpm=rpm or CONSERVATIVE_DEFAULT_LIMITS["rpm"],
            tpm=tpm,
            concurrency=conc or CONSERVATIVE_DEFAULT_LIMITS["concurrency"],
            source=source,
        )


def _int(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None


class Reservation(ContractModel):
    reservation_id: BoundedStr
    scopes: list[BoundedStr]
    tokens_reserved: int = 0
    units_reserved: float = 0
    acquired_at: datetime


class Denied(ContractModel):
    scope: BoundedStr
    dimension: Dim
    limit: float
    current: float
    retry_after_ms: int | None = None
    reason: BoundedStr


@dataclass
class ScopeSnapshot:
    scope: str
    limits: Limits
    rpm_used: int = 0
    tpm_used: int = 0
    in_flight: int = 0
    budget_used: float = 0.0
    breaker_open_until: datetime | None = None
    recent_failures: list[bool] = field(default_factory=list)


class AdmissionPort(Protocol):
    backend_name: str

    async def reserve(
        self, scopes: list[str], *, tokens_estimate: int = 0, units: float = 0.0
    ) -> Reservation | Denied: ...
    async def settle(
        self,
        reservation: Reservation,
        *,
        tokens_actual: int | None = None,
        units_actual: float | None = None,
        success: bool = True,
        category: str | None = None,
    ) -> None: ...
    async def release(self, reservation: Reservation) -> None: ...
    def set_limits(self, scope: str, limits: Limits) -> None: ...
    def snapshot(self) -> list[JsonObject]: ...


def rotate_on_failure(rt: object, provider_key: str | None, report: object) -> str | None:
    """CE-24/K: after a failed execution, ask the credential pool to rotate when the
    category indicts the credential. Returns the (possibly remapped) breaker category:
    ``credential_rotated`` when a healthy account took over (so the provider breaker does
    not count a single bad key), otherwise the original ``failure_category``."""
    cat = failure_category(report)
    pool = getattr(rt, "extensions", {}).get("credential_pool") if rt is not None else None
    if pool is None or cat is None:
        return cat
    nodes = getattr(report, "nodes", None) or []
    node = nodes[-1] if nodes else None
    if not provider_key:
        # /v1/execute: resolve the provider that actually failed from the last attempt
        attempts = getattr(node, "attempts", None) or []
        index = getattr(rt, "extensions", {}).get("catalog_index")
        if attempts and index is not None:
            provider_key = index.provider_key(attempts[-1].candidate.provider_id)
    if not provider_key:
        return cat
    err = getattr(getattr(node, "response", None), "error", None)
    retry = getattr(err, "retry_after_ms", None) if err is not None else None
    outcome = pool.on_failure(provider_key, cat, retry_after_ms=retry)
    return "credential_rotated" if outcome == "credential_rotated" else cat


def failure_category(report: object) -> str | None:
    """Provider error category of the last node of an ExecutionReport (None on success /
    unknown). Shared by every surface so the admission breaker sees the same signal."""
    nodes = getattr(report, "nodes", None) or []
    node = nodes[-1] if nodes else None
    resp = getattr(node, "response", None)
    err = getattr(resp, "error", None)
    if err is not None:
        code = getattr(err, "provider_code", None)
        if isinstance(code, str) and "context_window_exceeded" in code:
            return "context_window"  # CE-24/G: request-indicting, own breaker key
        return str(getattr(getattr(err, "category", None), "value", None) or "") or None
    node_err = getattr(getattr(node, "node", None), "error", None)
    if isinstance(node_err, dict):
        code = node_err.get("provider_code")
        if isinstance(code, str) and "context_window_exceeded" in code:
            return "context_window"
        cat = node_err.get("category")
        return str(cat) if cat else None
    return None
