"""CredentialPool (CE-24/K) — several credentials per provider with rotation and a
per-account breaker (litellm "multiple deployments / key rotation" pattern).

Upstream execution reads ``Runtime.credential_refs[provider_id]`` for every attempt. The
pool owns the extra refs and swaps the *active* one in that mapping, so upstream contracts
stay untouched. Rotation is driven by the same normalized error categories the admission
breaker sees:

* ``invalid_credential`` / ``auth_expired`` → the account is parked for ``AUTH_PARK_S``;
* ``quota_exceeded`` → parked for ``QUOTA_PARK_S``;
* ``rate_limited`` → parked for ``retry_after_ms`` (or ``RATE_PARK_S``);
* anything else → no rotation (the provider, not the key, is unhealthy).

With a single credential the pool is a no-op (legacy behaviour). The provider-level
admission breaker still applies, but ``failure_category`` on a *rotated* attempt is
reported as ``credential_rotated`` so one bad key never opens the whole provider while
healthy keys remain.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from core.contracts.base import JsonObject

AUTH_PARK_S = 600.0
QUOTA_PARK_S = 600.0
RATE_PARK_S = 30.0
ROTATE_CATEGORIES = frozenset(
    {"invalid_credential", "auth_expired", "quota_exceeded", "rate_limited"}
)


@dataclass
class Account:
    ref: str
    label: str
    parked_until: datetime | None = None
    failures: int = 0
    rotations_in: int = 0  # how many times this account became active
    last_error: str | None = None

    def available(self, now: datetime) -> bool:
        return self.parked_until is None or self.parked_until <= now


@dataclass
class ProviderPool:
    provider_key: str
    accounts: list[Account]
    active: int = 0
    rotations: int = 0


@dataclass
class CredentialPool:
    credential_refs: Any  # Runtime.credential_refs (MutableMapping[UUID, str])
    _pools: dict[UUID, ProviderPool] = field(default_factory=dict)
    _by_key: dict[str, UUID] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # -- setup -----------------------------------------------------------------------------

    def register(
        self, provider_id: UUID, provider_key: str, refs: list[str], labels: list[str] | None = None
    ) -> None:
        """Declare the credential refs for a provider (first one is active). One ref ⇒ no pool."""
        if not refs:
            return
        labels = labels or [f"acct-{i + 1}" for i in range(len(refs))]
        with self._lock:
            self._pools[provider_id] = ProviderPool(
                provider_key=provider_key,
                accounts=[Account(ref=r, label=labels[i]) for i, r in enumerate(refs)],
            )
            self._by_key[provider_key] = provider_id
            self.credential_refs[provider_id] = refs[0]

    def add(
        self, provider_id: UUID, provider_key: str, ref: str, label: str | None = None
    ) -> JsonObject:
        """Append a credential to a provider (adopting its current single ref first)."""
        self.adopt(provider_id, provider_key)
        with self._lock:
            pool = self._pools[provider_id]
            pool.accounts.append(Account(ref=ref, label=label or f"acct-{len(pool.accounts) + 1}"))
        return self.describe(provider_key)

    def adopt(self, provider_id: UUID, provider_key: str) -> None:
        """Make an existing single-credential provider poolable (its current ref = acct-1)."""
        if provider_id in self._pools:
            return
        ref = self.credential_refs.get(provider_id)
        if ref is None:
            return
        self.register(provider_id, provider_key, [ref])

    # -- rotation --------------------------------------------------------------------------

    def size(self, provider_key: str) -> int:
        pid = self._by_key.get(provider_key)
        return len(self._pools[pid].accounts) if pid is not None else 0

    def active_label(self, provider_key: str) -> str | None:
        pid = self._by_key.get(provider_key)
        if pid is None:
            return None
        pool = self._pools[pid]
        return pool.accounts[pool.active].label

    def on_failure(
        self, provider_key: str, category: str | None, *, retry_after_ms: int | None = None
    ) -> str | None:
        """Park the active account for a credential-indicting failure and rotate to the next
        available one. Returns ``"credential_rotated"`` when a healthy account took over,
        ``"credentials_exhausted"`` when every account is parked, ``None`` when the category
        does not concern credentials or the provider has a single credential."""
        pid = self._by_key.get(provider_key)
        if pid is None or category not in ROTATE_CATEGORIES:
            return None
        now = datetime.now(UTC)
        with self._lock:
            pool = self._pools[pid]
            if len(pool.accounts) < 2:
                return None
            cur = pool.accounts[pool.active]
            cur.failures += 1
            cur.last_error = category
            park = (
                AUTH_PARK_S
                if category in ("invalid_credential", "auth_expired")
                else QUOTA_PARK_S
                if category == "quota_exceeded"
                else (retry_after_ms / 1000 if retry_after_ms else RATE_PARK_S)
            )
            cur.parked_until = now + timedelta(seconds=park)
            n = len(pool.accounts)
            for step in range(1, n):
                idx = (pool.active + step) % n
                if pool.accounts[idx].available(now):
                    pool.active = idx
                    pool.accounts[idx].rotations_in += 1
                    pool.rotations += 1
                    self.credential_refs[pid] = pool.accounts[idx].ref
                    return "credential_rotated"
            return "credentials_exhausted"

    def unpark_expired(self) -> None:
        now = datetime.now(UTC)
        with self._lock:
            for pool in self._pools.values():
                for a in pool.accounts:
                    if a.parked_until is not None and a.parked_until <= now:
                        a.parked_until = None

    # -- read ------------------------------------------------------------------------------

    def describe(self, provider_key: str) -> JsonObject:
        pid = self._by_key.get(provider_key)
        if pid is None:
            return {"provider_key": provider_key, "accounts": [], "active": None, "rotations": 0}
        now = datetime.now(UTC)
        pool = self._pools[pid]
        return {
            "provider_key": provider_key,
            "active": pool.accounts[pool.active].label,
            "rotations": pool.rotations,
            "accounts": [
                {
                    "label": a.label,
                    "active": i == pool.active,
                    "available": a.available(now),
                    "parked_until": a.parked_until.isoformat() if a.parked_until else None,
                    "failures": a.failures,
                    "rotations_in": a.rotations_in,
                    "last_error": a.last_error,
                }
                for i, a in enumerate(pool.accounts)
            ],
        }

    def snapshot(self) -> list[JsonObject]:
        return [self.describe(k) for k in sorted(self._by_key)]


__all__ = ["AUTH_PARK_S", "QUOTA_PARK_S", "RATE_PARK_S", "ROTATE_CATEGORIES", "CredentialPool"]
