"""KeyService — in-memory virtual-key store (CE-24/D).

* keys are ``ce_live_<32 url-safe chars>``; only ``sha256(key)`` is stored;
* a master key (``CE_MASTER_KEY`` env, or generated at boot and logged ONCE when the
  mode is not ``off``) may manage keys; ordinary keys may only read their own record;
* each key is an admission scope ``key:<key_id>`` with the key's own limits;
* spend is accumulated from the UsageRecorder's cost (same pricing refs as /v1/usage).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from chat_engine.admission.port import Limits
from chat_engine.auth.contracts import (
    KeyCreate,
    KeyCreated,
    KeyRecord,
    KeySpend,
    KeyUpdate,
    Principal,
)

log = logging.getLogger("chat_engine.auth")

KEY_PREFIX = "ce_live_"


class AuthMode(StrEnum):
    OFF = "off"
    OPTIONAL = "optional"
    REQUIRED = "required"

    @classmethod
    def from_env(cls, raw: str | None) -> AuthMode:
        v = (raw or "off").strip().lower()
        try:
            return cls(v)
        except ValueError as exc:
            raise ValueError(f"CE_AUTH_MODE must be off|optional|required, got {raw!r}") from exc


class KeyNotFound(LookupError):
    pass


class KeyRejected(PermissionError):
    """Key exists but may not be used: disabled / expired / budget exhausted / model or
    project not allowed. ``reason`` is a stable machine string."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class KeyService:
    def __init__(
        self,
        *,
        tenant_id: UUID,
        mode: AuthMode = AuthMode.OFF,
        master_key: str | None = None,
        admission: object | None = None,
    ) -> None:
        self.mode = mode
        self._tenant = tenant_id
        self._admission = admission
        self._by_hash: dict[str, KeyRecord] = {}
        self._by_id: dict[UUID, KeyRecord] = {}
        self._lock = threading.Lock()
        self.master_generated = False
        self.master_key_id: UUID | None = None
        if mode is not AuthMode.OFF:
            if not master_key:
                master_key = KEY_PREFIX + secrets.token_urlsafe(24)
                self.master_generated = True
                # printed once so an operator can bootstrap; never persisted
                log.warning("CE_MASTER_KEY not set — generated master key: %s", master_key)
            rec = self._insert(
                master_key,
                KeyCreate(name="master", metadata={"role": "master"}),
                is_master=True,
            )
            self.master_key_id = rec.key_id

    # -- admin -----------------------------------------------------------------------------

    def _insert(self, key: str, req: KeyCreate, *, is_master: bool = False) -> KeyRecord:
        now = datetime.now(UTC)
        rec = KeyRecord(
            key_id=uuid4(),
            name=req.name,
            prefix=key[: len(KEY_PREFIX) + 6] + "…",
            tenant_id=req.tenant_id or self._tenant,
            project_ids=list(req.project_ids),
            models_allowed=list(req.models_allowed),
            rpm=req.rpm,
            tpm=req.tpm,
            rpd=req.rpd,
            tpd=req.tpd,
            concurrency=req.concurrency,
            budget_usd=req.budget_usd,
            expires_at=req.expires_at,
            is_master=is_master,
            created_at=now,
            metadata=dict(req.metadata),
        )
        with self._lock:
            self._by_hash[_hash(key)] = rec
            self._by_id[rec.key_id] = rec
        self._apply_limits(rec)
        return rec

    def create(self, req: KeyCreate) -> KeyCreated:
        key = KEY_PREFIX + secrets.token_urlsafe(24)
        rec = self._insert(key, req)
        return KeyCreated(key=key, record=rec)

    def get(self, key_id: UUID) -> KeyRecord:
        rec = self._by_id.get(key_id)
        if rec is None:
            raise KeyNotFound(str(key_id))
        return rec

    def list(self) -> list[KeyRecord]:
        return sorted(self._by_id.values(), key=lambda r: r.created_at)

    def update(self, key_id: UUID, req: KeyUpdate) -> KeyRecord:
        rec = self.get(key_id)
        patch = req.model_dump(exclude_none=True)
        new = rec.model_copy(update=patch)
        with self._lock:
            self._by_id[key_id] = new
            for h, r in list(self._by_hash.items()):
                if r.key_id == key_id:
                    self._by_hash[h] = new
        self._apply_limits(new)
        return new

    def delete(self, key_id: UUID) -> None:
        rec = self.get(key_id)
        if rec.is_master:
            raise KeyRejected("master_key", "the master key cannot be deleted")
        with self._lock:
            self._by_id.pop(key_id, None)
            for h, r in list(self._by_hash.items()):
                if r.key_id == key_id:
                    self._by_hash.pop(h, None)

    def _apply_limits(self, rec: KeyRecord) -> None:
        adm = self._admission
        if adm is None or not hasattr(adm, "set_limits"):
            return
        if all(v is None for v in (rec.rpm, rec.tpm, rec.concurrency, rec.rpd, rec.tpd)):
            # unlimited key: a wide-open scope so reserve() never denies on it
            adm.set_limits(
                self.scope(rec.key_id),
                Limits(rpm=None, tpm=None, concurrency=None, source="key_unlimited"),
            )
            return
        adm.set_limits(
            self.scope(rec.key_id),
            Limits(
                rpm=rec.rpm,
                tpm=rec.tpm,
                concurrency=rec.concurrency,
                rpd=rec.rpd,
                tpd=rec.tpd,
                source="key",
            ),
        )

    @staticmethod
    def scope(key_id: UUID) -> str:
        return f"key:{key_id}"

    # -- request path ----------------------------------------------------------------------

    def resolve(self, bearer: str | None) -> Principal | None:
        """``None`` when no key was presented. Raises KeyNotFound / KeyRejected."""
        if not bearer:
            return None
        rec = self._by_hash.get(_hash(bearer.strip()))
        if rec is None:
            raise KeyNotFound("unknown key")
        if rec.disabled:
            raise KeyRejected("key_disabled", "this key is disabled")
        if rec.expires_at is not None and rec.expires_at <= datetime.now(UTC):
            raise KeyRejected("key_expired", "this key has expired")
        if rec.budget_usd is not None and rec.spend.cost_usd >= rec.budget_usd:
            raise KeyRejected("budget_exhausted", "this key's budget is exhausted")
        return Principal(
            key_id=rec.key_id,
            tenant_id=rec.tenant_id,
            name=rec.name,
            is_master=rec.is_master,
            anonymous=False,
            project_ids=list(rec.project_ids),
            models_allowed=list(rec.models_allowed),
        )

    def anonymous(self) -> Principal:
        return Principal(tenant_id=self._tenant, anonymous=True)

    @staticmethod
    def check_model(principal: Principal, model_key: str | None) -> None:
        if principal.anonymous or not principal.models_allowed or model_key is None:
            return
        allowed = set(principal.models_allowed)
        if model_key in allowed or model_key.split("/")[-1] in allowed:
            return
        raise KeyRejected("model_not_allowed", f"model '{model_key}' is not allowed for this key")

    @staticmethod
    def check_project(principal: Principal, project_id: UUID | None) -> None:
        if principal.anonymous or not principal.project_ids or project_id is None:
            return
        if project_id not in set(principal.project_ids):
            raise KeyRejected("project_not_allowed", "project is not allowed for this key")

    def record_spend(
        self, key_id: UUID | None, *, tokens_in: int, tokens_out: int, cost_usd: float
    ) -> None:
        if key_id is None:
            return
        with self._lock:
            rec = self._by_id.get(key_id)
            if rec is None:
                return
            sp = rec.spend
            new = rec.model_copy(
                update={
                    "spend": KeySpend(
                        requests=sp.requests + 1,
                        tokens_in=sp.tokens_in + max(0, tokens_in),
                        tokens_out=sp.tokens_out + max(0, tokens_out),
                        cost_usd=round(sp.cost_usd + max(0.0, cost_usd), 8),
                        last_used_at=datetime.now(UTC),
                    )
                }
            )
            self._by_id[key_id] = new
            for h, r in list(self._by_hash.items()):
                if r.key_id == key_id:
                    self._by_hash[h] = new


__all__ = ["KEY_PREFIX", "AuthMode", "KeyNotFound", "KeyRejected", "KeyService"]
