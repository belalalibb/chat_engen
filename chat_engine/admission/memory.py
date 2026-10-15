"""InMemoryAdmission — default backend (single process; ``replicas_safe=false``).

Adds what upstream's ResourceSignalBoard lacks: TPM, concurrency, budget, and
a RELATIVE failure breaker (litellm's ``allowed_fails_policy`` pattern — N
failures within the window open the breaker, not a single error). Upstream's
own cooldown windows (30s/60s) remain in force inside the Router via signals.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import uuid4

from core.contracts.base import JsonObject

from chat_engine.admission.port import (
    WINDOW_SECONDS,
    Denied,
    Dim,
    Limits,
    Reservation,
    ScopeSnapshot,
)
from chat_engine.util.time import utc_now

BREAKER_FAILS = 5  # failures in the last BREAKER_SAMPLE attempts
BREAKER_SAMPLE = 10
BREAKER_OPEN = timedelta(seconds=30)


class InMemoryAdmission:
    backend_name = "in_memory"

    def __init__(
        self, *, clock: Callable[[], datetime] = utc_now, default_limits: Limits | None = None
    ) -> None:
        self._clock = clock
        self._default = default_limits or Limits.conservative()
        self._scopes: dict[str, ScopeSnapshot] = {}
        self._windows: dict[str, list[tuple[datetime, int]]] = {}  # scope → [(at, tokens)]
        self._reservations: dict[str, tuple[list[str], int, float]] = {}
        self._lock = threading.Lock()  # cheap; asyncio single loop but API may thread

    # -- config ------------------------------------------------------------------------

    def set_limits(self, scope: str, limits: Limits) -> None:
        snap = self._scopes.setdefault(scope, ScopeSnapshot(scope=scope, limits=limits))
        snap.limits = limits

    def _snap(self, scope: str) -> ScopeSnapshot:
        return self._scopes.setdefault(scope, ScopeSnapshot(scope=scope, limits=self._default))

    def _roll(self, scope: str, now: datetime) -> tuple[int, int]:
        cutoff = now - timedelta(seconds=WINDOW_SECONDS)
        rows = [r for r in self._windows.get(scope, []) if r[0] > cutoff]
        self._windows[scope] = rows
        return len(rows), sum(t for _, t in rows)

    # -- port ---------------------------------------------------------------------------

    async def reserve(
        self, scopes: list[str], *, tokens_estimate: int = 0, units: float = 0.0
    ) -> Reservation | Denied:
        now = self._clock()
        with self._lock:
            # phase 1: check every scope (all-or-nothing)
            for scope in scopes:
                snap = self._snap(scope)
                if snap.breaker_open_until and snap.breaker_open_until > now:
                    retry = int((snap.breaker_open_until - now).total_seconds() * 1000)
                    return Denied(
                        scope=scope,
                        dimension=Dim.RPM,
                        limit=0,
                        current=0,
                        retry_after_ms=retry,
                        reason="circuit breaker open (recent failures)",
                    )
                rpm_used, tpm_used = self._roll(scope, now)
                lim = snap.limits
                if lim.rpm is not None and rpm_used + 1 > lim.rpm:
                    oldest = self._windows[scope][0][0] if self._windows.get(scope) else now
                    retry = max(
                        0,
                        int(
                            (oldest + timedelta(seconds=WINDOW_SECONDS) - now).total_seconds()
                            * 1000
                        ),
                    )
                    return Denied(
                        scope=scope,
                        dimension=Dim.RPM,
                        limit=lim.rpm,
                        current=rpm_used,
                        retry_after_ms=retry,
                        reason="requests per minute exhausted",
                    )
                if lim.tpm is not None and tokens_estimate and tpm_used + tokens_estimate > lim.tpm:
                    return Denied(
                        scope=scope,
                        dimension=Dim.TPM,
                        limit=lim.tpm,
                        current=tpm_used,
                        retry_after_ms=WINDOW_SECONDS * 1000,
                        reason="tokens per minute exhausted",
                    )
                if lim.concurrency is not None and snap.in_flight + 1 > lim.concurrency:
                    return Denied(
                        scope=scope,
                        dimension=Dim.CONCURRENCY,
                        limit=lim.concurrency,
                        current=snap.in_flight,
                        retry_after_ms=250,
                        reason="concurrency limit reached",
                    )
                if lim.budget is not None and units and snap.budget_used + units > lim.budget:
                    return Denied(
                        scope=scope,
                        dimension=Dim.BUDGET,
                        limit=lim.budget,
                        current=snap.budget_used,
                        retry_after_ms=None,
                        reason="budget exhausted",
                    )
            # phase 2: apply
            rid = str(uuid4())
            for scope in scopes:
                snap = self._snap(scope)
                snap.in_flight += 1
                snap.budget_used += units
                self._windows.setdefault(scope, []).append((now, tokens_estimate))
            self._reservations[rid] = (list(scopes), tokens_estimate, units)
            return Reservation(
                reservation_id=rid,
                scopes=list(scopes),
                tokens_reserved=tokens_estimate,
                units_reserved=units,
                acquired_at=now,
            )

    async def settle(
        self,
        reservation: Reservation,
        *,
        tokens_actual: int | None = None,
        units_actual: float | None = None,
        success: bool = True,
    ) -> None:
        with self._lock:
            entry = self._reservations.pop(reservation.reservation_id, None)
            if entry is None:
                return
            scopes, _tokens_est, units = entry
            for scope in scopes:
                snap = self._snap(scope)
                snap.in_flight = max(0, snap.in_flight - 1)
                if units_actual is not None:
                    snap.budget_used += units_actual - units
                if tokens_actual is not None and self._windows.get(scope):
                    # replace the estimate with the truth on the most recent row
                    at, _ = self._windows[scope][-1]
                    self._windows[scope][-1] = (at, tokens_actual)
                snap.recent_failures = ([*snap.recent_failures, not success])[-BREAKER_SAMPLE:]
                if sum(snap.recent_failures) >= BREAKER_FAILS:
                    snap.breaker_open_until = self._clock() + BREAKER_OPEN
                    snap.recent_failures = []

    async def release(self, reservation: Reservation) -> None:
        """Undo a reservation that never ran (routing failed after admission)."""
        with self._lock:
            entry = self._reservations.pop(reservation.reservation_id, None)
            if entry is None:
                return
            scopes, _tokens, units = entry
            for scope in scopes:
                snap = self._snap(scope)
                snap.in_flight = max(0, snap.in_flight - 1)
                snap.budget_used = max(0.0, snap.budget_used - units)
                if self._windows.get(scope):
                    self._windows[scope].pop()

    def snapshot(self) -> list[JsonObject]:
        now = self._clock()
        out: list[JsonObject] = []
        for scope, snap in sorted(self._scopes.items()):
            rpm_used, tpm_used = self._roll(scope, now)
            out.append(
                {
                    "scope": scope,
                    "limits": snap.limits.model_dump(exclude_none=True),
                    "rpm_used": rpm_used,
                    "tpm_used": tpm_used,
                    "in_flight": snap.in_flight,
                    "budget_used": round(snap.budget_used, 3),
                    "breaker_open": bool(snap.breaker_open_until and snap.breaker_open_until > now),
                }
            )
        return out


async def with_jitter(base_ms: int, attempt: int, *, cap_ms: int = 5_000) -> None:
    """Exponential backoff with full jitter (retry-storm protection)."""
    import random

    delay = min(cap_ms, base_ms * (2**attempt))
    await asyncio.sleep(random.uniform(0, delay) / 1000)
