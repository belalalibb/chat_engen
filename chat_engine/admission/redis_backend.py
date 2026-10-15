"""RedisAdmission — multi-replica safe backend (``ADMISSION_REDIS_URL``).

One Lua script performs check-and-apply for ALL scopes atomically (no race
between replicas). Uses the ``redis`` package already shipped by upstream
(ADR-0003) — no new dependency. Tests run against a real Redis when
``REDIS_TEST_URL`` is set and are skipped otherwise (honest: no fakeredis).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import uuid4

from core.contracts.base import JsonObject

from chat_engine.admission.port import WINDOW_SECONDS, Denied, Dim, Limits, Reservation
from chat_engine.util.time import utc_now

RESERVE_LUA = """
-- KEYS: for each scope i: {scope}:rpm (zset), {scope}:tpm (zset w/ token score), {scope}:conc (string), {scope}:budget (string), {scope}:lim (hash)
-- ARGV: now_ms, window_ms, tokens, units, nscopes, default_rpm, default_conc, rid
local now = tonumber(ARGV[1]); local win = tonumber(ARGV[2]); local tokens = tonumber(ARGV[3]); local units = tonumber(ARGV[4])
local n = tonumber(ARGV[5]); local drpm = tonumber(ARGV[6]); local dconc = tonumber(ARGV[7]); local rid = ARGV[8]
local cutoff = now - win
for i = 0, n - 1 do
  local krpm, ktpm, kconc, kbud, klim = KEYS[i*5+1], KEYS[i*5+2], KEYS[i*5+3], KEYS[i*5+4], KEYS[i*5+5]
  redis.call('ZREMRANGEBYSCORE', krpm, 0, cutoff)
  redis.call('ZREMRANGEBYSCORE', ktpm, 0, cutoff)
  local lim = redis.call('HGETALL', klim)
  local L = {}
  for j = 1, #lim, 2 do L[lim[j]] = tonumber(lim[j+1]) end
  local rpm = L['rpm'] or drpm; local conc = L['concurrency'] or dconc; local tpm = L['tpm']; local budget = L['budget']
  local rpm_used = redis.call('ZCARD', krpm)
  if rpm_used + 1 > rpm then
    local oldest = redis.call('ZRANGE', krpm, 0, 0, 'WITHSCORES')
    local retry = 0
    if oldest[2] then retry = math.max(0, tonumber(oldest[2]) + win - now) end
    return {'denied', tostring(i), 'rpm', tostring(rpm), tostring(rpm_used), tostring(retry)}
  end
  if tpm and tokens > 0 then
    local used = 0
    local rows = redis.call('ZRANGE', ktpm, 0, -1)
    for _, m in ipairs(rows) do local t = string.match(m, ':(%d+)$'); used = used + (tonumber(t) or 0) end
    if used + tokens > tpm then return {'denied', tostring(i), 'tpm', tostring(tpm), tostring(used), tostring(win)} end
  end
  local inflight = tonumber(redis.call('GET', kconc) or '0')
  if inflight + 1 > conc then return {'denied', tostring(i), 'concurrency', tostring(conc), tostring(inflight), '250'} end
  if budget and units > 0 then
    local bused = tonumber(redis.call('GET', kbud) or '0')
    if bused + units > budget then return {'denied', tostring(i), 'budget', tostring(budget), tostring(bused), '-1'} end
  end
end
for i = 0, n - 1 do
  local krpm, ktpm, kconc, kbud = KEYS[i*5+1], KEYS[i*5+2], KEYS[i*5+3], KEYS[i*5+4]
  redis.call('ZADD', krpm, now, rid)
  redis.call('ZADD', ktpm, now, rid .. ':' .. tostring(tokens))
  redis.call('INCR', kconc)
  if units > 0 then redis.call('INCRBYFLOAT', kbud, units) end
  redis.call('PEXPIRE', krpm, win * 2); redis.call('PEXPIRE', ktpm, win * 2)
end
return {'ok', rid}
"""


class RedisAdmission:
    backend_name = "redis"

    def __init__(
        self,
        url: str,
        *,
        prefix: str = "ce:adm",
        clock: Callable[[], datetime] = utc_now,
        default_limits: Limits | None = None,
        client: Any | None = None,
    ) -> None:
        import redis.asyncio as aioredis

        self._r = client if client is not None else aioredis.from_url(url, decode_responses=True)
        self._prefix = prefix
        self._clock = clock
        self._default = default_limits or Limits.conservative()
        self._script = self._r.register_script(RESERVE_LUA)
        self._known_scopes: set[str] = set()
        self._pending: set[Any] = set()

    def _keys(self, scope: str) -> list[str]:
        p = f"{self._prefix}:{scope}"
        return [f"{p}:rpm", f"{p}:tpm", f"{p}:conc", f"{p}:budget", f"{p}:lim"]

    def set_limits(self, scope: str, limits: Limits) -> None:
        import asyncio

        self._known_scopes.add(scope)
        mapping = {k: v for k, v in limits.model_dump(exclude_none=True).items() if k != "source"}
        key = self._keys(scope)[4]
        loop = asyncio.get_event_loop()
        coro = self._r.hset(key, mapping=mapping) if mapping else self._r.delete(key)
        if loop.is_running():
            task = loop.create_task(coro)
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)
        else:
            loop.run_until_complete(coro)

    async def set_limits_async(self, scope: str, limits: Limits) -> None:
        self._known_scopes.add(scope)
        mapping = {k: v for k, v in limits.model_dump(exclude_none=True).items() if k != "source"}
        key = self._keys(scope)[4]
        if mapping:
            await self._r.hset(key, mapping=mapping)
        else:
            await self._r.delete(key)

    async def reserve(
        self, scopes: list[str], *, tokens_estimate: int = 0, units: float = 0.0
    ) -> Reservation | Denied:
        self._known_scopes.update(scopes)
        now = self._clock()
        now_ms = int(now.timestamp() * 1000)
        keys = [k for s in scopes for k in self._keys(s)]
        rid = str(uuid4())
        res = await self._script(
            keys=keys,
            args=[
                now_ms,
                WINDOW_SECONDS * 1000,
                tokens_estimate,
                units,
                len(scopes),
                self._default.rpm or 60,
                self._default.concurrency or 4,
                rid,
            ],
        )
        if res[0] == "denied":
            idx, dim, limit, current, retry = (
                int(res[1]),
                res[2],
                float(res[3]),
                float(res[4]),
                int(float(res[5])),
            )
            return Denied(
                scope=scopes[idx],
                dimension=Dim(dim),
                limit=limit,
                current=current,
                retry_after_ms=None if retry < 0 else retry,
                reason=f"{dim} limit reached",
            )
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
        pipe = self._r.pipeline()
        for scope in reservation.scopes:
            _krpm, ktpm, kconc, kbud, _ = self._keys(scope)
            pipe.decr(kconc)
            if units_actual is not None and units_actual != reservation.units_reserved:
                pipe.incrbyfloat(kbud, units_actual - reservation.units_reserved)
            if tokens_actual is not None and tokens_actual != reservation.tokens_reserved:
                pipe.zrem(ktpm, f"{reservation.reservation_id}:{reservation.tokens_reserved}")
                pipe.zadd(
                    ktpm,
                    {
                        f"{reservation.reservation_id}:{tokens_actual}": int(
                            reservation.acquired_at.timestamp() * 1000
                        )
                    },
                )
        await pipe.execute()

    async def release(self, reservation: Reservation) -> None:
        pipe = self._r.pipeline()
        for scope in reservation.scopes:
            krpm, ktpm, kconc, kbud, _ = self._keys(scope)
            pipe.decr(kconc)
            pipe.zrem(krpm, reservation.reservation_id)
            pipe.zrem(ktpm, f"{reservation.reservation_id}:{reservation.tokens_reserved}")
            if reservation.units_reserved:
                pipe.incrbyfloat(kbud, -reservation.units_reserved)
        await pipe.execute()

    def snapshot(self) -> list[JsonObject]:
        return [{"scope": s, "backend": "redis"} for s in sorted(self._known_scopes)]

    async def snapshot_async(self) -> list[JsonObject]:
        out: list[JsonObject] = []
        for s in sorted(self._known_scopes):
            krpm, _ktpm, kconc, kbud, klim = self._keys(s)
            out.append(
                {
                    "scope": s,
                    "rpm_used": await self._r.zcard(krpm),
                    "in_flight": int(await self._r.get(kconc) or 0),
                    "budget_used": float(await self._r.get(kbud) or 0),
                    "limits": await self._r.hgetall(klim),
                }
            )
        return out
