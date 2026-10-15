"""BYOK credentials (CE-05): the secret enters ONCE, leaves as ``credential_ref``.

Storage is upstream's ``SecretManagerPort`` (in-memory by default; Vault via
upstream's infrastructure binding when configured). chat_engine persists only
the METADATA row (owner, provider hint, label, last4 fingerprint) — never the
value, never a reversible form.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from core.contracts.base import BoundedStr, ContractModel
from core.contracts.domain import CredentialPolicy, OwnerType
from core.secrets.memory import InMemorySecretManager
from pydantic import Field

from chat_engine.util.time import utc_now


class CredentialState(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class CredentialCreateRequest(ContractModel):
    """The ONLY place a raw key is accepted. It is not echoed back."""

    secret: str = Field(min_length=8, max_length=4096)
    label: BoundedStr
    owner_type: OwnerType = OwnerType.USER
    provider_hint: BoundedStr | None = None
    policy: CredentialPolicy = CredentialPolicy.PREFER_USER


class CredentialRecord(ContractModel):
    id: UUID
    credential_ref: BoundedStr
    label: BoundedStr
    owner_type: OwnerType
    owner_id: UUID | None = None
    provider_hint: BoundedStr | None = None
    policy: CredentialPolicy
    state: CredentialState
    fingerprint: BoundedStr  # sha256(secret)[:12] — enough to tell two keys apart, never to recover one
    last4: BoundedStr
    created_at: datetime
    revoked_at: datetime | None = None


class CredentialListResponse(ContractModel):
    credentials: list[CredentialRecord]


class CredentialNotFound(LookupError):
    pass


class CredentialService:
    def __init__(self, secrets: InMemorySecretManager, *, tenant_id: UUID, user_id: UUID) -> None:
        self._secrets = secrets
        self._tenant = tenant_id
        self._user = user_id
        self._rows: dict[UUID, CredentialRecord] = {}

    def create(self, req: CredentialCreateRequest) -> CredentialRecord:
        ref = self._secrets.store(self._tenant, req.secret)
        row = CredentialRecord(
            id=uuid4(),
            credential_ref=ref,
            label=req.label,
            owner_type=req.owner_type,
            owner_id=self._user if req.owner_type is OwnerType.USER else None,
            provider_hint=req.provider_hint,
            policy=req.policy,
            state=CredentialState.ACTIVE,
            fingerprint=hashlib.sha256(req.secret.encode()).hexdigest()[:12],
            last4=req.secret[-4:],
            created_at=utc_now(),
        )
        self._rows[row.id] = row
        return row

    def list(self) -> list[CredentialRecord]:
        return sorted(self._rows.values(), key=lambda r: r.created_at)

    def get(self, credential_id: UUID) -> CredentialRecord:
        row = self._rows.get(credential_id)
        if row is None:
            raise CredentialNotFound(str(credential_id))
        return row

    def by_ref(self, credential_ref: str) -> CredentialRecord | None:
        return next((r for r in self._rows.values() if r.credential_ref == credential_ref), None)

    def revoke(self, credential_id: UUID) -> CredentialRecord:
        row = self.get(credential_id)
        if row.state is CredentialState.REVOKED:
            return row
        try:
            self._secrets.revoke(self._tenant, row.credential_ref)
        except Exception:  # noqa: BLE001 — already gone upstream: idempotent revoke
            pass
        updated = row.model_copy(update={"state": CredentialState.REVOKED, "revoked_at": utc_now()})
        self._rows[credential_id] = updated
        return updated
