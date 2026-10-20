"""CE-24/F: exact-match cache — off by default; on only for deterministic requests;
hit = no provider call, usage.cached, metadata.cache_hit, zero-cost usage record; TTL/LRU."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from chat_engine.api.app import create_app
from chat_engine.cache import CacheEntry, ExactMatchCache, cache_key, cacheable
from chat_engine.composition.runtime import build_runtime
from fastapi.testclient import TestClient

from tests.conftest import HERMETIC_ENV
from tests.orchestration.test_strategies import add_scripted


def test_cacheable_rules_and_key() -> None:
    assert not cacheable(None) and not cacheable({}) and not cacheable({"temperature": 0.7})
    assert cacheable({"temperature": 0}) and cacheable({"temperature": 0.0})
    assert cacheable({"cache": True, "temperature": 1}) and not cacheable(
        {"cache": False, "temperature": 0}
    )
    k1 = cache_key(
        model_key="m",
        provider_key=None,
        messages=[{"role": "user", "content": "a"}],
        generation={"temperature": 0, "cache": True},
    )
    k2 = cache_key(
        model_key="m",
        provider_key=None,
        messages=[{"role": "user", "content": "a"}],
        generation={"temperature": 0},
    )
    assert k1 == k2  # the cache flag itself is not part of the key
    assert k1 != cache_key(
        model_key="m2",
        provider_key=None,
        messages=[{"role": "user", "content": "a"}],
        generation={"temperature": 0},
    )
    assert k1 != cache_key(
        model_key="m",
        provider_key=None,
        messages=[{"role": "user", "content": "b"}],
        generation={"temperature": 0},
    )


def test_cache_ttl_lru_and_env() -> None:
    c = ExactMatchCache(enabled=True, ttl_s=60, max_entries=2)
    e = CacheEntry(
        content="x",
        provider_key="p",
        model_key="m",
        usage={},
        execution_id="e",
        stored_at=datetime.now(UTC),
    )
    c.put("a", e)
    c.put("b", e)
    c.put("c", e)  # evicts a
    assert c.get("a") is None and c.get("b") is not None and c.stats["evictions"] == 1
    old = CacheEntry(
        content="x",
        provider_key="p",
        model_key="m",
        usage={},
        execution_id="e",
        stored_at=datetime.now(UTC) - timedelta(seconds=120),
    )
    c.put("old", old)
    assert c.get("old") is None  # expired
    assert c.snapshot()["enabled"] is True and c.clear() >= 1
    off = ExactMatchCache.from_env({})
    assert not off.enabled and off.get("a") is None
    off.put("a", e)
    assert off.snapshot()["entries"] == 0
    assert (
        ExactMatchCache.from_env({"CE_CACHE": "memory", "CE_CACHE_TTL_S": "5"}).ttl.total_seconds()
        == 5
    )
    with pytest.raises(ValueError):
        ExactMatchCache.from_env({"CE_CACHE": "redis"})


def test_cache_off_by_default(client, runtime) -> None:
    ad = add_scripted(runtime, "cp", ["c-m"], {"c-m": "deterministic"})
    for _ in range(2):
        r = client.post(
            "/v1/chat", json={"message": "same", "model": "c-m", "generation": {"temperature": 0}}
        )
        assert r.status_code == 200 and "cached" not in r.json()["usage"]
    assert len(ad.calls) == 2
    assert client.get("/v1/admin/cache").json()["enabled"] is False


def test_cache_hit_end_to_end() -> None:
    rt = build_runtime({**HERMETIC_ENV, "CE_CACHE": "memory"})
    ad = add_scripted(rt, "cp", ["c-m"], {"c-m": "deterministic"})
    c = TestClient(create_app(rt))
    body = {"message": "same question", "model": "c-m", "generation": {"temperature": 0}}
    r1 = c.post("/v1/chat", json=body)
    assert r1.status_code == 200 and r1.json()["usage"].get("cached") is None
    r2 = c.post("/v1/chat", json=body)  # new conversation, same compiled prompt → hit
    assert r2.status_code == 200, r2.text
    assert (
        r2.json()["usage"]["cached"] is True and r2.json()["turn"]["metadata"]["cache_hit"] is True
    )
    assert r2.json()["turn"]["parts"][0]["text"] == "deterministic"
    assert r2.json()["execution_id"] == r1.json()["execution_id"]
    assert len(ad.calls) == 1  # provider called once
    # non-deterministic request bypasses the cache
    r3 = c.post("/v1/chat", json={**body, "generation": {"temperature": 0.9}})
    assert r3.status_code == 200 and len(ad.calls) == 2
    # different message misses
    r4 = c.post("/v1/chat", json={**body, "message": "other"})
    assert r4.status_code == 200 and len(ad.calls) == 3
    snap = c.get("/v1/admin/cache").json()
    assert snap["enabled"] and snap["hits"] == 1 and snap["entries"] == 2
    recs = c.get("/v1/usage/records", params={"limit": 50}).json()["records"]
    cached = [x for x in recs if x["status"] == "cached"]
    assert (
        len(cached) == 1 and cached[0]["cost"]["amount"] == 0 and cached[0]["metadata"]["cache_hit"]
    )
    assert c.delete("/v1/admin/cache").json()["cleared"] == 2
    r5 = c.post("/v1/chat", json=body)
    assert r5.status_code == 200 and len(ad.calls) == 4
