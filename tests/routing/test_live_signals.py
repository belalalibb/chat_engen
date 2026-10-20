"""CE-24/C: live-signal reordering — EWMA/in-flight bookkeeping, banded reorder with
hysteresis, flag off ⇒ byte-identical decisions, and the before/after measurement the
plan demands (p50/p95 over a slow vs fast provider pair) written to evidence/ce24/."""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import uuid4

from core.contracts.provider import ProviderGenerateRequest, ProviderGenerateResponse
from fastapi.testclient import TestClient

from chat_engine.api.app import create_app
from chat_engine.composition.runtime import build_runtime
from chat_engine.routing.live_signals import MIN_GAIN, SCORE_BAND, LiveSignals
from tests.conftest import HERMETIC_ENV
from tests.orchestration.test_strategies import ScriptedAdapter, add_scripted

ROOT = Path(__file__).resolve().parents[2]


class Cand:
    def __init__(self, pid, score, reasons=None):
        self.provider_id, self.score, self.reasons = pid, score, list(reasons or [])
        self.model_id = uuid4()

    def model_copy(self, update):
        c = Cand(self.provider_id, self.score, update.get("reasons", self.reasons))
        c.model_id = self.model_id
        return c


class Dec:
    def __init__(self, cands):
        self.selected, self.fallback_candidates = cands[0], cands[1:]

    def model_copy(self, update):
        d = Dec([self.selected, *self.fallback_candidates])
        for k, v in update.items():
            setattr(d, k, v)
        return d


def test_ewma_and_inflight() -> None:
    ls = LiveSignals(enabled=True)
    ls.observe("a", 100, success=True)
    ls.observe("a", 300, success=True)
    assert ls.effective_ms("a") == 100 * 0.7 + 300 * 0.3
    ls.start("a")
    assert ls.effective_ms("a") == (100 * 0.7 + 300 * 0.3) * 1.5
    ls.finish("a", latency_ms=None, success=False)
    snap = {s["provider_key"]: s for s in ls.snapshot()}
    assert snap["a"]["in_flight"] == 0 and snap["a"]["errors"] == 1 and snap["a"]["samples"] == 2
    assert ls.effective_ms("unknown") is None


def test_reorder_rules() -> None:
    pa, pb, pc = uuid4(), uuid4(), uuid4()
    keys = {pa: "a", pb: "b", pc: "c"}
    ls = LiveSignals(enabled=False)
    dec = Dec([Cand(pa, 0.9), Cand(pb, 0.88), Cand(pc, 0.5)])
    assert ls.reorder(dec, keys.get) is dec  # disabled ⇒ identity
    ls.enabled = True
    assert ls.reorder(dec, keys.get) is dec  # no observations ⇒ identity
    ls.observe("a", 1000, success=True)
    ls.observe("b", 200, success=True)
    out = ls.reorder(dec, keys.get)
    assert out.selected.provider_id == pb and "live_signals" in out.selected.reasons[-1]
    assert [c.provider_id for c in out.fallback_candidates] == [pa, pc]  # c outside band stays last
    # hysteresis: tiny gain does not flip
    ls2 = LiveSignals(enabled=True)
    ls2.observe("a", 1000, success=True)
    ls2.observe("b", 1000 * (1 - MIN_GAIN / 2), success=True)
    assert ls2.reorder(dec, keys.get) is dec
    # a clearly better upstream score is never overridden by latency
    far = Dec([Cand(pa, 0.9), Cand(pb, 0.9 * (1 - SCORE_BAND) - 0.01)])
    ls3 = LiveSignals(enabled=True)
    ls3.observe("a", 5000, success=True)
    ls3.observe("b", 10, success=True)
    assert ls3.reorder(far, keys.get) is far


# --- measured before/after ------------------------------------------------------------------------


class LatencyAdapter(ScriptedAdapter):
    def __init__(self, manifest, delay_ms: int):
        super().__init__(manifest, {})
        self.delay_ms = delay_ms

    async def generate(self, request: ProviderGenerateRequest) -> ProviderGenerateResponse:
        self.calls.append({"model": request.provider_model_name})
        await asyncio.sleep(self.delay_ms / 1000)
        return ProviderGenerateResponse(
            request_id=request.request_id,
            succeeded=True,
            output={"content": "ok"},
            usage={"total_tokens": 10, "units": 1},
            latency_ms=self.delay_ms,
        )


def _measure(enabled: bool, n: int = 12) -> dict:
    rt = build_runtime({**HERMETIC_ENV, "CE_ROUTING_LIVE_SIGNALS": "1" if enabled else "0"})
    from core.contracts.domain import AuthType, Provider, ProviderStatus

    from chat_engine.composition.runtime import echo_manifest

    adapters = {}
    for key, delay in (("slow", 60), ("fast", 5)):
        manifest = echo_manifest().model_copy(update={"id": key, "name": key})
        adapters[key] = LatencyAdapter(manifest, delay)
        rt.register_provider(
            provider=Provider(
                id=uuid4(),
                provider_key=key,
                display_name=key,
                status=ProviderStatus.ACTIVE,
                auth_types=[AuthType.API_KEY],
                supports_account_pool=False,
            ),
            manifest=manifest,
            adapter=adapters[key],
            credential_ref=f"secret-ref://{key}",
            model_names=["shared-m"],
        )
        rt.extensions["capabilities"].record_external_verification(key, "chat", passed=True, detail="t")
    c = TestClient(create_app(rt))
    # warm-up: one call per provider so both have an observation (probe both explicitly)
    for key in ("slow", "fast"):
        assert c.post("/v1/chat", json={"message": "warm", "model": "shared-m", "provider": key}).status_code == 200
    lat = []
    picks = []
    for _ in range(n):
        t0 = time.perf_counter()
        r = c.post("/v1/chat", json={"message": "hi", "model": "shared-m"})
        lat.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200, r.text
        picks.append(r.json()["provider_key"])
    lat.sort()
    return {
        "enabled": enabled,
        "n": n,
        "p50_ms": round(statistics.median(lat), 1),
        "p95_ms": round(lat[int(0.95 * (len(lat) - 1))], 1),
        "picks": {k: picks.count(k) for k in sorted(set(picks))},
    }


def test_measured_before_after_written_to_evidence() -> None:
    before = _measure(False)
    after = _measure(True)
    # with the flag, the fast provider must dominate; without it upstream order rules
    assert after["picks"].get("fast", 0) >= 0.8 * after["n"], after
    assert after["p50_ms"] < before["p50_ms"] or before["picks"].get("fast", 0) >= 0.8 * before["n"]
    out = ROOT / "evidence" / "ce24" / "live_signals_before_after.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"before": before, "after": after}, indent=2) + "\n")


def test_admin_signals_endpoint(client, runtime) -> None:
    add_scripted(runtime, "sig", ["sig-m"], {"sig-m": "ok"})
    assert client.post("/v1/chat", json={"message": "x", "model": "sig-m"}).status_code == 200
    body = client.get("/v1/admin/routing/signals").json()
    assert body["enabled"] is False
    row = next(p for p in body["providers"] if p["provider_key"] == "sig")
    assert row["samples"] == 1 and row["in_flight"] == 0


def test_decision_untouched_when_disabled(runtime) -> None:
    rt = runtime
    add_scripted(rt, "d1", ["dm"], {"dm": "a"})
    add_scripted(rt, "d2", ["dm"], {"dm": "b"})
    chat = rt.extensions["chat"]
    d = chat.route("dm", None, [])
    assert rt.reorder_decision(d) is d
    assert isinstance(rt.extensions["live_signals"], LiveSignals)
    assert NS is not None
