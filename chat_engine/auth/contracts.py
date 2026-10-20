"""Contracts for virtual keys (CE-24/D). Additive module; ContractModel ⇒ extra=forbid."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from core.contracts.base import BoundedStr, ContractModel, JsonObject
from pydantic import Field


class KeyCreate(ContractModel):
    """``POST /v1/admin/keys`` body. Every limit is optional (None = unlimited)."""

    name: BoundedStr
    tenant_id: UUID | None = None  # None ⇒ the process tenant
    project_ids: list[UUID] = Field(default_factory=list)  # [] ⇒ any project
    models_allowed: list[BoundedStr] = Field(default_factory=list)  # [] ⇒ any model
    rpm: int | None = Field(default=None, ge=1)
    tpm: int | None = Field(default=None, ge=1)
    rpd: int | None = Field(default=None, ge=1)
    tpd: int | None = Field(default=None, ge=1)
    concurrency: int | None = Field(default=None, ge=1)
    budget_usd: float | None = Field(default=None, ge=0)
    expires_at: datetime | None = None
    metadata: JsonObject = Field(default_factory=dict)


class KeyUpdate(ContractModel):
    name: BoundedStr | None = None
    models_allowed: list[BoundedStr] | None = None
    project_ids: list[UUID] | None = None
    rpm: int | None = Field(default=None, ge=1)
    tpm: int | None = Field(default=None, ge=1)
    rpd: int | None = Field(default=None, ge=1)
    tpd: int | None = Field(default=None, ge=1)
    concurrency: int | None = Field(default=None, ge=1)
    budget_usd: float | None = Field(default=None, ge=0)
    expires_at: datetime | None = None
    disabled: bool | None = None
    metadata: JsonObject | None = None


class KeySpend(ContractModel):
    requests: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_total: int = 0
    cost_usd: float = 0.0
    last_used_at: datetime | None = None


class KeyRecord(ContractModel):
    """What admins see. The secret is never stored or returned — only ``key_hash``'s
    prefix (``ce_live_ab12…``) for recognition."""

    key_id: UUID
    name: BoundedStr
    prefix: BoundedStr
    tenant_id: UUID
    project_ids: list[UUID] = Field(default_factory=list)
    models_allowed: list[BoundedStr] = Field(default_factory=list)
    rpm: int | None = None
    tpm: int | None = None
    rpd: int | None = None
    tpd: int | None = None
    concurrency: int | None = None
    budget_usd: float | None = None
    expires_at: datetime | None = None
    disabled: bool = False
    is_master: bool = False
    created_at: datetime
    spend: KeySpend = Field(default_factory=KeySpend)
    metadata: JsonObject = Field(default_factory=dict)


class KeyCreated(ContractModel):
    """Returned ONCE from ``POST /v1/admin/keys`` — the only time the secret is visible."""

    key: BoundedStr
    record: KeyRecord


class Principal(ContractModel):
    """Resolved caller (request.state.principal). ``anonymous`` when no key was sent."""

    key_id: UUID | None = None
    tenant_id: UUID
    name: BoundedStr | None = None
    is_master: bool = False
    anonymous: bool = True
    project_ids: list[UUID] = Field(default_factory=list)
    models_allowed: list[BoundedStr] = Field(default_factory=list)


__all__ = ["KeyCreate", "KeyCreated", "KeyRecord", "KeySpend", "KeyUpdate", "Principal"]
