"""Idempotency store (D5): key + request hash, 24h TTL; same key ≠ hash ⇒ 409."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from chat_engine.util.time import utc_now

TTL = timedelta(hours=24)


class IdempotencyConflict(ValueError):
    def __init__(self, key: str, existing: UUID) -> None:
        self.key, self.existing = key, existing
        super().__init__(f"idempotency key reused with a different request: {key}")


class IdempotencyStore:
    def __init__(self, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._rows: dict[tuple[UUID, str], tuple[str, UUID, datetime]] = {}
        self._clock = clock

    def lookup(self, tenant_id: UUID, key: str, request_hash: str) -> UUID | None:
        row = self._rows.get((tenant_id, key))
        if row is None:
            return None
        stored_hash, execution_id, at = row
        if self._clock() - at > TTL:
            del self._rows[(tenant_id, key)]
            return None
        if stored_hash != request_hash:
            raise IdempotencyConflict(key, execution_id)
        return execution_id

    def remember(self, tenant_id: UUID, key: str, request_hash: str, execution_id: UUID) -> None:
        self._rows[(tenant_id, key)] = (request_hash, execution_id, self._clock())
