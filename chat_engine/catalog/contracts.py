"""Canonical model identity (ADR-CE-0001 D1) — chat_engine-owned contracts.

Five identities stay SEPARATE: CanonicalModel (new), Model.model_key
(upstream routing key, untouched), ModelAlias (new), ProviderModelBinding
(upstream), Credential (upstream). Two model_keys appear as ONE catalog row
only through ``ModelAlias.decision == confirmed`` — never by name similarity.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from core.contracts.base import BoundedStr, ContractModel, JsonObject
from pydantic import Field


class AliasDecision(StrEnum):
    CONFIRMED = "confirmed"
    PROPOSED = "proposed"
    REJECTED = "rejected"


class AliasSource(StrEnum):
    MANUAL = "manual"
    CATALOG_MODELS_DEV = "catalog:models.dev"
    CATALOG_LITELLM = "catalog:litellm"  # CE-24/A secondary source (proposals only)
    DISCOVERY = "discovery"
    SYSTEM = "system"  # the identity alias every model_key gets for itself


class CanonicalModel(ContractModel):
    """Provider-neutral model identity: ``canonical_id`` like ``openai/gpt-4o``."""

    canonical_id: BoundedStr
    display_name: BoundedStr
    family: BoundedStr | None = None
    vendor: BoundedStr | None = None
    context_window: int | None = Field(default=None, ge=1)
    output_limit: int | None = Field(default=None, ge=1)
    modalities_in: list[BoundedStr] = Field(default_factory=list)
    modalities_out: list[BoundedStr] = Field(default_factory=list)
    capabilities: list[BoundedStr] = Field(default_factory=list)
    release_date: BoundedStr | None = None
    source: AliasSource = AliasSource.SYSTEM
    source_ref: BoundedStr | None = None
    verified_at: datetime | None = None


class ModelAlias(ContractModel):
    """``alias`` (a registry ``model_key`` or a provider model name) → canonical."""

    alias: BoundedStr
    canonical_id: BoundedStr
    provider_key: BoundedStr | None = None
    decision: AliasDecision
    confidence: float = Field(ge=0.0, le=1.0)
    source: AliasSource
    reason: BoundedStr | None = None
    decided_by: BoundedStr | None = None
    decided_at: datetime | None = None


class CatalogProviderRef(ContractModel):
    provider_key: BoundedStr
    model_key: BoundedStr
    provider_model_name: BoundedStr
    availability: BoundedStr
    eligible_now: bool
    reason: BoundedStr | None = None
    retry_after_ms: int | None = None


class CatalogEntry(ContractModel):
    """One row of GET /v1/catalog/models — a canonical model shown ONCE."""

    canonical: CanonicalModel
    model_keys: list[BoundedStr]
    configured_provider_count: int = Field(ge=0)
    eligible_provider_count: int = Field(ge=0)
    proposed_alias_count: int = Field(ge=0)
    providers: list[CatalogProviderRef] = Field(default_factory=list)


class CatalogPage(ContractModel):
    items: list[CatalogEntry]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
    built_at: datetime
    index_stats: JsonObject = Field(default_factory=dict)


class AliasDecisionRequest(ContractModel):
    decision: AliasDecision
    canonical_id: BoundedStr | None = None
    reason: BoundedStr | None = None
    decided_by: BoundedStr = "admin"


class AliasListResponse(ContractModel):
    aliases: list[ModelAlias]
    total: int = Field(ge=0)
