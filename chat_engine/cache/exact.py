"""ExactMatchCache — litellm-style deterministic response cache (CE-24/F).

* key = sha256(model_key, provider_key?, compiled messages, generation) — the *compiled*
  prompt (after handoff/skills/guardrails), so two users with different projects never
  collide;
* only used when the request is deterministic: ``generation.temperature == 0`` or an
  explicit ``generation.cache: true``; everything else bypasses the cache;
* ``CE_CACHE=off`` (default) | ``memory``; ``CE_CACHE_TTL_S`` (default 300),
  ``CE_CACHE_MAX`` entries (default 1000, LRU);
* a hit is marked honestly: ``usage.cached = true`` and ``turn.metadata.cache_hit`` — no
  provider call, no admission reservation, no spend, and the usage record says so.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from core.contracts.base import JsonObject

DEFAULT_TTL_S = 300
DEFAULT_MAX = 1000


def cacheable(generation: JsonObject | None) -> bool:
    gen = generation or {}
    if gen.get("cache") is True:
        return True
    if gen.get("cache") is False:
        return False
    temp = gen.get("temperature")
    return isinstance(temp, int | float) and not isinstance(temp, bool) and float(temp) == 0.0


def cache_key(
    *,
    model_key: str | None,
    provider_key: str | None,
    messages: list[JsonObject],
    generation: JsonObject | None,
    tools: list[JsonObject] | None = None,
) -> str:
    gen = {k: v for k, v in (generation or {}).items() if k != "cache"}
    blob = json.dumps(
        {
            "model": model_key,
            "provider": provider_key,
            "messages": messages,
            "generation": gen,
            "tools": tools or [],
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class CacheEntry:
    content: str
    provider_key: str | None
    model_key: str | None
    usage: JsonObject
    execution_id: str | None
    stored_at: datetime
    hits: int = 0
    extra: JsonObject = field(default_factory=dict)


class ExactMatchCache:
    def __init__(self, *, enabled: bool = False, ttl_s: int = DEFAULT_TTL_S, max_entries: int = DEFAULT_MAX):
        self.enabled = enabled
        self.ttl = timedelta(seconds=max(1, ttl_s))
        self.max_entries = max(1, max_entries)
        self._rows: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self.stats = {"hits": 0, "misses": 0, "stores": 0, "evictions": 0}

    @classmethod
    def from_env(cls, env: Any) -> ExactMatchCache:
        mode = str(env.get("CE_CACHE", "off") or "off").strip().lower()
        if mode not in ("off", "memory"):
            raise ValueError("CE_CACHE must be off|memory")
        return cls(
            enabled=mode == "memory",
            ttl_s=int(env.get("CE_CACHE_TTL_S", DEFAULT_TTL_S) or DEFAULT_TTL_S),
            max_entries=int(env.get("CE_CACHE_MAX", DEFAULT_MAX) or DEFAULT_MAX),
        )

    def get(self, key: str) -> CacheEntry | None:
        if not self.enabled:
            return None
        now = datetime.now(UTC)
        with self._lock:
            row = self._rows.get(key)
            if row is None:
                self.stats["misses"] += 1
                return None
            if now - row.stored_at > self.ttl:
                self._rows.pop(key, None)
                self.stats["misses"] += 1
                return None
            self._rows.move_to_end(key)
            row.hits += 1
            self.stats["hits"] += 1
            return row

    def put(self, key: str, entry: CacheEntry) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._rows[key] = entry
            self._rows.move_to_end(key)
            self.stats["stores"] += 1
            while len(self._rows) > self.max_entries:
                self._rows.popitem(last=False)
                self.stats["evictions"] += 1

    def clear(self) -> int:
        with self._lock:
            n = len(self._rows)
            self._rows.clear()
            return n

    def snapshot(self) -> JsonObject:
        return {
            "enabled": self.enabled,
            "ttl_s": int(self.ttl.total_seconds()),
            "max_entries": self.max_entries,
            "entries": len(self._rows),
            **self.stats,
        }


__all__ = ["DEFAULT_MAX", "DEFAULT_TTL_S", "CacheEntry", "ExactMatchCache", "cache_key", "cacheable"]
