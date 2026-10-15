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


class Dim(StrEnum):
    RPM = "rpm"
    TPM = "tpm"
    CONCURRENCY = "concurrency"
    BUDGET = "budget"


class Limits(ContractModel):
    rpm: int | None = Field(default=None, ge=1)
    tpm: int | None = Field(default=None, ge=1)
    concurrency: int | None = Field(default=None, ge=1)
    budget: float | None = Field(default=None, ge=0)
    source: BoundedStr = "conservative_default"

    @classmethod
    def conservative(cls) -> Limits:
        return cls(rpm=CONSERVATIVE_DEFAULT_LIMITS["rpm"], concurrency=CONSERVATIVE_DEFAULT_LIMITS["concurrency"], source="conservative_default")

    @classmethod
    def from_metadata(cls, meta: JsonObject | None, *, source: str = "provider_metadata") -> Limits:
        """Read upstream ``limits_metadata`` ({rpm|requests_per_minute, tpm|tokens_per_minute, concurrency})."""
        meta = meta or {}
        rpm = _int(meta.get("rpm", meta.get("requests_per_minute")))
        tpm = _int(meta.get("tpm", meta.get("tokens_per_minute")))
        conc = _int(meta.get("concurrency", meta.get("max_concurrency")))
        if rpm is None and tpm is None and conc is None:
            return cls.conservative()
        return cls(rpm=rpm or CONSERVATIVE_DEFAULT_LIMITS["rpm"], tpm=tpm, concurrency=conc or CONSERVATIVE_DEFAULT_LIMITS["concurrency"], source=source)


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

    async def reserve(self, scopes: list[str], *, tokens_estimate: int = 0, units: float = 0.0) -> Reservation | Denied: ...
    async def settle(self, reservation: Reservation, *, tokens_actual: int | None = None, units_actual: float | None = None, success: bool = True) -> None: ...
    async def release(self, reservation: Reservation) -> None: ...
    def set_limits(self, scope: str, limits: Limits) -> None: ...
    def snapshot(self) -> list[JsonObject]: ...
