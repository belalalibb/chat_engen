"""Provider configuration lifecycle (ADR-CE-0001 D2) — append-only revisions.

States: draft → validating → discovered → verified → enabled | failed | disabled | archived.
Every transition appends a ``ProviderConfigRevision`` (never mutates history);
``rollback`` re-applies an earlier revision's config as a NEW revision.

Only ``enabled`` providers are registered in the upstream registries (and thus
routable). Disabling removes the registration; the revision log stays.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import httpx
from core.contracts.base import BoundedStr, ContractModel, JsonObject
from core.contracts.domain import AuthType, CredentialStatus, Provider, ProviderStatus
from core.contracts.provider import DiscoveredModel, ProviderCapabilities
from pydantic import Field

from chat_engine.providers.openai_compatible import OpenAICompatibleAdapter, build_manifest
from chat_engine.util.time import utc_now

if TYPE_CHECKING:
    from chat_engine.composition.runtime import Runtime


class ProviderState(StrEnum):
    DRAFT = "draft"
    VALIDATING = "validating"
    DISCOVERED = "discovered"
    VERIFIED = "verified"
    ENABLED = "enabled"
    FAILED = "failed"
    DISABLED = "disabled"
    ARCHIVED = "archived"


ALLOWED: dict[ProviderState, frozenset[ProviderState]] = {
    ProviderState.DRAFT: frozenset({ProviderState.VALIDATING, ProviderState.ARCHIVED}),
    ProviderState.VALIDATING: frozenset({ProviderState.DISCOVERED, ProviderState.FAILED}),
    ProviderState.DISCOVERED: frozenset({ProviderState.VERIFIED, ProviderState.FAILED, ProviderState.VALIDATING}),
    ProviderState.VERIFIED: frozenset({ProviderState.ENABLED, ProviderState.DISABLED, ProviderState.VALIDATING}),
    ProviderState.ENABLED: frozenset({ProviderState.DISABLED, ProviderState.VALIDATING}),
    ProviderState.FAILED: frozenset({ProviderState.VALIDATING, ProviderState.ARCHIVED, ProviderState.DRAFT}),
    ProviderState.DISABLED: frozenset({ProviderState.ENABLED, ProviderState.VALIDATING, ProviderState.ARCHIVED}),
    ProviderState.ARCHIVED: frozenset(),
}


class ProviderConfig(ContractModel):
    """What the admin supplies. NEVER carries a secret — only ``credential_ref``."""

    provider_key: BoundedStr
    display_name: BoundedStr
    kind: BoundedStr = "openai_compatible"
    base_url: BoundedStr
    credential_ref: BoundedStr | None = None
    static_models: list[BoundedStr] = Field(default_factory=list)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    capabilities: JsonObject = Field(default_factory=lambda: {"chat": True, "tool_use": True})
    limits: JsonObject = Field(default_factory=dict)  # {rpm, tpm, concurrency} — unknown ⇒ conservative defaults (CE-07)
    notes: BoundedStr | None = None


class ProviderConfigRevision(ContractModel):
    provider_key: BoundedStr
    revision: int = Field(ge=1)
    config_hash: BoundedStr
    state: ProviderState
    changed_by: BoundedStr
    changed_at: datetime
    reason: BoundedStr | None = None
    config: ProviderConfig
    discovered_models: list[BoundedStr] = Field(default_factory=list)
    detail: BoundedStr | None = None


class ProviderSummary(ContractModel):
    provider_key: BoundedStr
    display_name: BoundedStr
    kind: BoundedStr
    state: ProviderState
    revision: int
    base_url: BoundedStr
    credential_configured: bool
    model_count: int
    discovered_models: list[BoundedStr] = Field(default_factory=list)
    registered: bool
    changed_at: datetime
    limits: JsonObject = Field(default_factory=dict)


class ProviderDraftRequest(ContractModel):
    display_name: BoundedStr
    base_url: BoundedStr
    kind: BoundedStr = "openai_compatible"
    credential_ref: BoundedStr | None = None
    static_models: list[BoundedStr] = Field(default_factory=list)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    capabilities: JsonObject | None = None
    limits: JsonObject = Field(default_factory=dict)
    notes: BoundedStr | None = None


class TransitionRequest(ContractModel):
    reason: BoundedStr | None = None
    changed_by: BoundedStr = "admin"


class RollbackRequest(ContractModel):
    revision: int = Field(ge=1)
    reason: BoundedStr | None = None
    changed_by: BoundedStr = "admin"


class LifecycleError(Exception):
    pass


class UnknownProvider(LifecycleError):
    pass


class InvalidTransition(LifecycleError):
    def __init__(self, provider_key: str, current: ProviderState, target: ProviderState) -> None:
        self.current, self.target = current, target
        super().__init__(f"{provider_key}: {current.value} → {target.value} is not allowed")


class ProviderConflict(LifecycleError):
    pass


def config_hash(config: ProviderConfig) -> str:
    canonical = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


@dataclass
class _Record:
    revisions: list[ProviderConfigRevision] = field(default_factory=list)
    provider_id: UUID | None = None  # set while registered in upstream registries
    adapter: OpenAICompatibleAdapter | None = None

    @property
    def head(self) -> ProviderConfigRevision:
        return self.revisions[-1]


class ProviderLifecycleService:
    """In-memory store (D10: ce_provider_revision table lands with the PG store)."""

    def __init__(
        self,
        rt: Runtime,
        *,
        transport_factory: Callable[[ProviderConfig], httpx.AsyncBaseTransport | None] | None = None,
    ) -> None:
        self._rt = rt
        self._records: dict[str, _Record] = {}
        self._transport_factory = transport_factory

    # -- queries ------------------------------------------------------------------------

    def list(self) -> list[ProviderSummary]:
        return sorted((self._summary(k) for k in self._records), key=lambda s: s.provider_key)

    def get(self, provider_key: str) -> ProviderSummary:
        self._require(provider_key)
        return self._summary(provider_key)

    def revisions(self, provider_key: str) -> list[ProviderConfigRevision]:
        return list(self._require(provider_key).revisions)

    def adapter_for(self, provider_key: str) -> OpenAICompatibleAdapter | None:
        return self._records.get(provider_key, _Record()).adapter

    # -- transitions ---------------------------------------------------------------------

    def draft(self, provider_key: str, req: ProviderDraftRequest, *, changed_by: str = "admin") -> ProviderConfigRevision:
        if provider_key in self._records and self._records[provider_key].head.state is not ProviderState.ARCHIVED:
            raise ProviderConflict(f"provider already exists: {provider_key}")
        if provider_key in self._rt.providers.all_keys() and provider_key not in self._records:
            raise ProviderConflict(f"provider key is bound by the composition (not managed here): {provider_key}")
        config = ProviderConfig(
            provider_key=provider_key,
            display_name=req.display_name,
            kind=req.kind,
            base_url=req.base_url,
            credential_ref=req.credential_ref,
            static_models=req.static_models,
            extra_headers=req.extra_headers,
            capabilities=req.capabilities or {"chat": True, "tool_use": True},
            limits=req.limits,
            notes=req.notes,
        )
        self._records[provider_key] = _Record()
        return self._append(provider_key, config, ProviderState.DRAFT, changed_by, "draft created")

    def update_draft(self, provider_key: str, req: ProviderDraftRequest, *, changed_by: str = "admin") -> ProviderConfigRevision:
        rec = self._require(provider_key)
        if rec.head.state not in (ProviderState.DRAFT, ProviderState.FAILED, ProviderState.DISABLED, ProviderState.VERIFIED, ProviderState.DISCOVERED):
            raise InvalidTransition(provider_key, rec.head.state, ProviderState.DRAFT)
        if rec.provider_id is not None:
            self._unregister(provider_key)
        config = rec.head.config.model_copy(update=req.model_dump(exclude_none=True, exclude={"capabilities"}) | ({"capabilities": req.capabilities} if req.capabilities else {}))
        return self._append(provider_key, config, ProviderState.DRAFT, changed_by, "draft updated")

    async def validate(self, provider_key: str, *, changed_by: str = "admin") -> ProviderConfigRevision:
        rec = self._require(provider_key)
        self._transition(provider_key, ProviderState.VALIDATING, changed_by, "validation started")
        config = rec.head.config
        if config.credential_ref is None:
            return self._transition(provider_key, ProviderState.FAILED, changed_by, "no credential_ref configured")
        if not self._rt.secrets.exists(self._rt.tenant_id, config.credential_ref):
            return self._transition(provider_key, ProviderState.FAILED, changed_by, "credential_ref does not resolve")
        adapter = self._build_adapter(config)
        health = await adapter.validate_credential(config.credential_ref)
        if health.status is not CredentialStatus.ACTIVE:
            return self._transition(provider_key, ProviderState.FAILED, changed_by, f"credential {health.status.value}: {health.detail or ''}".strip())
        discovered = await adapter.discover_models()
        names = sorted({d.provider_model_name for d in discovered} | set(config.static_models))
        if not names:
            return self._transition(provider_key, ProviderState.FAILED, changed_by, "no models discovered and no static_models declared")
        rec.adapter = adapter
        return self._transition(provider_key, ProviderState.DISCOVERED, changed_by, f"{len(names)} model(s)", discovered=names, detail=health.detail)

    async def verify(self, provider_key: str, *, changed_by: str = "admin", probe_model: str | None = None) -> ProviderConfigRevision:
        """One tiny real generation — proves the endpoint answers the dialect."""
        rec = self._require(provider_key)
        if rec.head.state is not ProviderState.DISCOVERED:
            raise InvalidTransition(provider_key, rec.head.state, ProviderState.VERIFIED)
        adapter = rec.adapter or self._build_adapter(rec.head.config)
        model = probe_model or (rec.head.discovered_models[0] if rec.head.discovered_models else None)
        if model is None:
            return self._transition(provider_key, ProviderState.FAILED, changed_by, "no model to probe")
        from core.contracts.provider import ProviderGenerateRequest, ProviderOperation

        resp = await adapter.generate(
            ProviderGenerateRequest(
                request_id=uuid4(),
                tenant_id=self._rt.tenant_id,
                operation=ProviderOperation.GENERATE_TEXT,
                provider_model_name=model,
                credential_ref=rec.head.config.credential_ref or "",
                payload={"ask": "Reply with the single word: ok", "generation": {"max_tokens": 8, "temperature": 0}},
                timeout_ms=30_000,
            )
        )
        if not resp.succeeded:
            err = resp.error
            return self._transition(provider_key, ProviderState.FAILED, changed_by, f"probe failed: {err.category.value if err else 'unknown'} — {err.safe_message if err else ''}")
        return self._transition(provider_key, ProviderState.VERIFIED, changed_by, f"probe ok on {model} ({resp.latency_ms} ms)", discovered=rec.head.discovered_models)

    def enable(self, provider_key: str, *, changed_by: str = "admin") -> ProviderConfigRevision:
        rec = self._require(provider_key)
        if rec.head.state not in (ProviderState.VERIFIED, ProviderState.DISABLED):
            raise InvalidTransition(provider_key, rec.head.state, ProviderState.ENABLED)
        self._register(provider_key)
        return self._transition(provider_key, ProviderState.ENABLED, changed_by, "registered in routing registries", discovered=rec.head.discovered_models)

    def disable(self, provider_key: str, *, changed_by: str = "admin", reason: str | None = None) -> ProviderConfigRevision:
        rec = self._require(provider_key)
        if rec.head.state not in (ProviderState.ENABLED, ProviderState.VERIFIED):
            raise InvalidTransition(provider_key, rec.head.state, ProviderState.DISABLED)
        self._unregister(provider_key)
        return self._transition(provider_key, ProviderState.DISABLED, changed_by, reason or "disabled by admin", discovered=rec.head.discovered_models)

    def archive(self, provider_key: str, *, changed_by: str = "admin", reason: str | None = None) -> ProviderConfigRevision:
        rec = self._require(provider_key)
        if rec.head.state is ProviderState.ENABLED:
            raise InvalidTransition(provider_key, rec.head.state, ProviderState.ARCHIVED)
        return self._transition(provider_key, ProviderState.ARCHIVED, changed_by, reason or "archived")

    def rollback(self, provider_key: str, revision: int, *, changed_by: str = "admin", reason: str | None = None) -> ProviderConfigRevision:
        """Re-apply an older revision's CONFIG as a new DRAFT revision (never rewrites history)."""
        rec = self._require(provider_key)
        target = next((r for r in rec.revisions if r.revision == revision), None)
        if target is None:
            raise UnknownProvider(f"{provider_key}: no revision {revision}")
        if rec.provider_id is not None:
            self._unregister(provider_key)
        return self._append(provider_key, target.config, ProviderState.DRAFT, changed_by, reason or f"rollback to revision {revision} (config {target.config_hash})")

    # -- internals ------------------------------------------------------------------------

    def _require(self, provider_key: str) -> _Record:
        rec = self._records.get(provider_key)
        if rec is None:
            raise UnknownProvider(provider_key)
        return rec

    def _append(self, provider_key: str, config: ProviderConfig, state: ProviderState, changed_by: str, reason: str | None, *, discovered: list[str] | None = None, detail: str | None = None) -> ProviderConfigRevision:
        rec = self._records[provider_key]
        rev = ProviderConfigRevision(
            provider_key=provider_key,
            revision=len(rec.revisions) + 1,
            config_hash=config_hash(config),
            state=state,
            changed_by=changed_by,
            changed_at=utc_now(),
            reason=reason,
            config=config,
            discovered_models=list(discovered or []),
            detail=detail,
        )
        rec.revisions.append(rev)
        return rev

    def _transition(self, provider_key: str, target: ProviderState, changed_by: str, reason: str | None, *, discovered: list[str] | None = None, detail: str | None = None) -> ProviderConfigRevision:
        rec = self._records[provider_key]
        current = rec.head.state
        if target not in ALLOWED[current]:
            raise InvalidTransition(provider_key, current, target)
        return self._append(provider_key, rec.head.config, target, changed_by, reason, discovered=discovered if discovered is not None else rec.head.discovered_models, detail=detail)

    def _build_adapter(self, config: ProviderConfig) -> OpenAICompatibleAdapter:
        tenant = self._rt.tenant_id
        secrets = self._rt.secrets

        def _resolve(ref: str) -> str:
            return str(secrets.resolve(tenant, ref))

        manifest = build_manifest(
            provider_key=config.provider_key,
            display_name=config.display_name,
            base_url=config.base_url,
            static_models=list(config.static_models),
            discovery="dynamic",
            capabilities=ProviderCapabilities.model_validate(config.capabilities),
        )
        return OpenAICompatibleAdapter(
            manifest,
            base_url=config.base_url,
            secret_resolver=_resolve,
            health_credential_ref=config.credential_ref,
            transport=self._transport_factory(config) if self._transport_factory else None,
            extra_headers=config.extra_headers,
        )

    def _register(self, provider_key: str) -> None:
        rec = self._records[provider_key]
        if rec.provider_id is not None:
            return
        config = rec.head.config
        adapter = rec.adapter or self._build_adapter(config)
        rec.adapter = adapter
        provider = Provider(
            id=uuid4(),
            provider_key=provider_key,
            display_name=config.display_name,
            status=ProviderStatus.ACTIVE,
            auth_types=[AuthType.API_KEY],
            supports_account_pool=False,
        )
        self._rt.register_provider(
            provider=provider,
            manifest=adapter.get_manifest(),
            adapter=adapter,
            credential_ref=config.credential_ref or "",
            model_names=list(rec.head.discovered_models or config.static_models),
        )
        rec.provider_id = provider.id

    def _unregister(self, provider_key: str) -> None:
        rec = self._records[provider_key]
        if rec.provider_id is None:
            return
        self._rt.unregister_provider(provider_key)
        rec.provider_id = None

    def _summary(self, provider_key: str) -> ProviderSummary:
        rec = self._records[provider_key]
        head = rec.head
        return ProviderSummary(
            provider_key=provider_key,
            display_name=head.config.display_name,
            kind=head.config.kind,
            state=head.state,
            revision=head.revision,
            base_url=head.config.base_url,
            credential_configured=head.config.credential_ref is not None,
            model_count=len(head.discovered_models or head.config.static_models),
            discovered_models=list(head.discovered_models),
            registered=rec.provider_id is not None,
            changed_at=head.changed_at,
            limits=dict(head.config.limits),
        )


__all__ = [
    "DiscoveredModel",
    "InvalidTransition",
    "LifecycleError",
    "ProviderConfig",
    "ProviderConfigRevision",
    "ProviderConflict",
    "ProviderDraftRequest",
    "ProviderLifecycleService",
    "ProviderState",
    "ProviderSummary",
    "RollbackRequest",
    "TransitionRequest",
    "UnknownProvider",
]
