"""CE-07 D5: atomic reserve/settle, conservative defaults, breaker, idempotency, storms."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from chat_engine.admission import (
    Denied,
    IdempotencyConflict,
    IdempotencyStore,
    InMemoryAdmission,
    Limits,
    Reservation,
)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def tick(self, s: float) -> None:
        self.now += timedelta(seconds=s)


async def test_unknown_limits_are_conservative_not_infinite() -> None:
    adm = InMemoryAdmission()
    scope = "provider:unknown"
    oks = [await adm.reserve([scope]) for _ in range(4)]
    assert all(isinstance(r, Reservation) for r in oks)
    fifth = await adm.reserve([scope])
    assert isinstance(fifth, Denied) and fifth.dimension.value == "concurrency" and fifth.limit == 4
    snap = adm.snapshot()[0]
    assert snap["limits"]["source"] == "conservative_default"


async def test_rpm_window_and_retry_after_with_fake_clock() -> None:
    clock = Clock()
    adm = InMemoryAdmission(clock=clock)
    adm.set_limits("p", Limits(rpm=3, concurrency=100, source="test"))
    for _ in range(3):
        r = await adm.reserve(["p"])
        assert isinstance(r, Reservation)
        await adm.settle(r)
    d = await adm.reserve(["p"])
    assert isinstance(d, Denied) and d.dimension.value == "rpm" and d.retry_after_ms == 60_000
    clock.tick(61)
    assert isinstance(await adm.reserve(["p"]), Reservation)


async def test_all_or_nothing_across_scopes() -> None:
    adm = InMemoryAdmission()
    adm.set_limits("tenant:t", Limits(rpm=100, concurrency=1, source="test"))
    adm.set_limits("provider:p", Limits(rpm=100, concurrency=5, source="test"))
    first = await adm.reserve(["tenant:t", "provider:p"])
    assert isinstance(first, Reservation)
    second = await adm.reserve(["tenant:t", "provider:p"])
    assert isinstance(second, Denied) and second.scope == "tenant:t"
    assert adm.snapshot()[0]["in_flight"] == 1  # provider:p NOT incremented by the denied attempt
    await adm.release(first)
    assert isinstance(await adm.reserve(["tenant:t", "provider:p"]), Reservation)


async def test_tpm_and_budget_dimensions() -> None:
    adm = InMemoryAdmission()
    adm.set_limits("p", Limits(rpm=100, concurrency=100, tpm=1000, budget=2.5, source="test"))
    r = await adm.reserve(["p"], tokens_estimate=800, units=1)
    assert isinstance(r, Reservation)
    d = await adm.reserve(["p"], tokens_estimate=300, units=1)
    assert isinstance(d, Denied) and d.dimension.value == "tpm"
    await adm.settle(r, tokens_actual=100, units_actual=1)
    r2 = await adm.reserve(["p"], tokens_estimate=300, units=1)
    assert isinstance(r2, Reservation)
    await adm.settle(r2)
    d2 = await adm.reserve(["p"], tokens_estimate=1, units=1)
    assert isinstance(d2, Denied) and d2.dimension.value == "budget"


async def test_relative_breaker_opens_after_repeated_failures() -> None:
    clock = Clock()
    adm = InMemoryAdmission(clock=clock)
    adm.set_limits("p", Limits(rpm=1000, concurrency=1000, source="test"))
    for _ in range(5):
        r = await adm.reserve(["p"])
        assert isinstance(r, Reservation)
        await adm.settle(r, success=False)
    d = await adm.reserve(["p"])
    assert isinstance(d, Denied) and "breaker" in d.reason and d.retry_after_ms == 30_000
    clock.tick(31)
    assert isinstance(await adm.reserve(["p"]), Reservation)


async def test_retry_storm_never_exceeds_concurrency() -> None:
    adm = InMemoryAdmission()
    adm.set_limits("p", Limits(rpm=100000, concurrency=8, source="test"))
    peak = 0
    inflight = 0
    admitted = 0

    async def worker() -> None:
        nonlocal peak, inflight, admitted
        for attempt in range(6):
            got = await adm.reserve(["p"])
            if isinstance(got, Reservation):
                inflight += 1
                peak = max(peak, inflight)
                admitted += 1
                await asyncio.sleep(0.002)
                inflight -= 1
                await adm.settle(got)
                return
            await asyncio.sleep(0.001 * (attempt + 1))

    await asyncio.gather(*(worker() for _ in range(200)))
    assert peak <= 8 and admitted > 8


def test_idempotency_conflict_on_same_key_different_hash() -> None:
    store = IdempotencyStore()
    t = uuid4()
    eid = uuid4()
    assert store.lookup(t, "k", "h1") is None
    store.remember(t, "k", "h1", eid)
    assert store.lookup(t, "k", "h1") == eid
    with pytest.raises(IdempotencyConflict):
        store.lookup(t, "k", "h2")


def test_execute_admission_denied_maps_to_rate_limited(client, runtime) -> None:
    adm = runtime.extensions["admission"]
    adm.set_limits("provider:local_echo", Limits(rpm=1, concurrency=10, source="test"))
    assert client.post("/v1/execute", json={"ask": "a"}).status_code == 200
    r = client.post("/v1/execute", json={"ask": "b"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert (
        r.json()["error"]["details"]["dimension"] == "rpm"
        and r.json()["error"]["retryable"] is True
    )
    snap = client.get("/v1/admin/admission").json()
    assert snap["backend"] == "in_memory" and snap["replicas_safe"] is False
