#!/usr/bin/env python3
"""CE-17 scale validation — reproducible numbers, no fakery.

Measures, on a hermetic runtime with N synthetic providers (StreamingEchoAdapter):
  * onboarding time + RSS for N providers
  * catalog `entries()` p50/p95 and `/v1/catalog/models` page p95
  * explicit routing p95 (one model served by many providers)
  * AUTO routing p95
  * concurrent streams first-byte p95 (service level)
  * admission under contention (200 workers)

Usage: python benchmarks/bench.py [--n 1000] [--streams 200] [--json out.json]
Budgets (CHAT_ENGINE_TASKS.md CE-17) are evaluated and printed as MET / GAP.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import resource
import statistics
import time
from uuid import uuid4

from core.contracts.domain import AuthType, Provider, ProviderStatus
from core.contracts.provider import ProviderOperation
from core.contracts.routing import RoutingRequest
from fastapi.testclient import TestClient

from chat_engine.api.app import create_app
from chat_engine.composition.runtime import (
    Runtime,
    StreamingEchoAdapter,
    build_runtime,
    echo_manifest,
)

BUDGETS_MS = {
    "catalog_page_p95": 50.0,
    "explicit_p95": 10.0,
    "auto_p95": 150.0,
    "stream_first_byte_p95": 200.0,
}
BUDGET_RSS_MB = 1536.0


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def add_provider(rt: Runtime, key: str, models: list[str]) -> None:
    manifest = echo_manifest().model_copy(update={"id": key, "name": key})
    adapter = StreamingEchoAdapter(manifest)
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
        adapter=adapter,
        credential_ref=f"secret-ref://{key}",
        model_names=models,
    )
    led = rt.extensions["capabilities"]
    led.record_external_verification(key, "chat", passed=True, detail="bench")
    led.declare(key, "streaming_text", True, source="admin")
    led.record_external_verification(key, "streaming_text", passed=True, detail="bench")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--streams", type=int, default=200)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    out: dict[str, object] = {"n_providers": a.n, "streams": a.streams}

    rt = build_runtime({})
    t0 = time.perf_counter()
    for i in range(a.n):
        add_provider(rt, f"bp{i:05d}", [f"bm-{i % 50:03d}", "shared-model"])
    out["onboard_s"] = round(time.perf_counter() - t0, 3)
    out["rss_mb_after_onboard"] = round(rss_mb(), 1)

    catalog = rt.extensions["catalog"]
    samples: list[float] = []
    for _ in range(7):
        t = time.perf_counter()
        catalog.entries()
        samples.append((time.perf_counter() - t) * 1000)
    out["catalog_entries_p50_ms"] = round(statistics.median(samples), 2)
    out["catalog_entries_p95_ms"] = round(pct(samples, 0.95), 2)

    client = TestClient(create_app(rt))
    samples = []
    for off in range(10):
        t = time.perf_counter()
        r = client.get("/v1/catalog/models", params={"offset": off * 50, "limit": 50})
        samples.append((time.perf_counter() - t) * 1000)
        assert r.status_code == 200
    out["catalog_page_p95_ms"] = round(pct(samples, 0.95), 2)

    pol = catalog.resolve_policy("shared-model")
    samples = []
    for _ in range(200):
        t = time.perf_counter()
        rt.router.route(RoutingRequest(operation=ProviderOperation.GENERATE_TEXT, model_policy=pol))
        samples.append((time.perf_counter() - t) * 1000)
    out["explicit_p95_ms"] = round(pct(samples, 0.95), 2)

    samples = []
    for _ in range(50):
        t = time.perf_counter()
        rt.router.route(RoutingRequest(operation=ProviderOperation.GENERATE_TEXT))
        samples.append((time.perf_counter() - t) * 1000)
    out["auto_p95_ms"] = round(pct(samples, 0.95), 2)

    svc = rt.extensions["streaming"]
    decision = rt.router.route(
        RoutingRequest(operation=ProviderOperation.GENERATE_TEXT, model_policy=pol)
    )
    payload = {"ask": "a b c d", "messages": [{"role": "user", "content": "a b c d"}]}

    async def one() -> float:
        t = time.perf_counter()
        async for ev in svc.run(decision=decision, payload=payload):
            if ev.type.value == "message_delta":
                return (time.perf_counter() - t) * 1000
        return (time.perf_counter() - t) * 1000

    async def many() -> list[float]:
        return list(await asyncio.gather(*(one() for _ in range(a.streams))))

    fb = asyncio.run(many())
    out["stream_first_byte_p95_ms"] = round(pct(fb, 0.95), 2)

    adm = rt.extensions["admission"]

    async def hammer() -> dict[str, int]:
        from chat_engine.admission import Denied

        counts = {"ok": 0, "denied": 0}

        async def w() -> None:
            got = await adm.reserve(
                ["tenant:bench", "provider:bp00000"], tokens_estimate=10, units=1.0
            )
            if isinstance(got, Denied):
                counts["denied"] += 1
            else:
                counts["ok"] += 1
                await adm.settle(got, tokens_actual=10, success=True)

        # burst: 200 workers at once against conservative limits (concurrency=4) —
        # all-or-nothing semantics mean most are denied; that is the contract, not a bug
        await asyncio.gather(*(w() for _ in range(200)))
        burst = dict(counts)
        counts["ok"] = counts["denied"] = 0
        sem = asyncio.Semaphore(4)

        async def paced() -> None:
            async with sem:
                await w()

        await asyncio.gather(*(paced() for _ in range(200)))
        return {"burst_200": burst, "paced_4_concurrent": dict(counts)}

    out["admission"] = asyncio.run(hammer())
    out["rss_mb_peak"] = round(rss_mb(), 1)

    verdicts = {
        k: ("MET" if float(out[k + "_ms"]) <= v else "GAP")  # type: ignore[arg-type]
        for k, v in BUDGETS_MS.items()
    }
    verdicts["rss_peak"] = "MET" if float(out["rss_mb_peak"]) <= BUDGET_RSS_MB else "GAP"  # type: ignore[arg-type]
    out["budgets"] = verdicts
    print(json.dumps(out, indent=2))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(out, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
