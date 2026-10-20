"""CE-24/B: per-category breaker policy (litellm allowed_fails_policy pattern).

* unset env ⇒ legacy relative breaker (5 of 10, 30s) — byte-for-byte old behaviour;
* a rule with fails=N opens after N failures of that category within 60s, for its own
  open_seconds; fails=0 ⇒ the category never counts (request's own fault);
* categories without a rule still fall back to the relative breaker;
* tenant/project scopes stay exempt; the orchestrator consults the admission breaker.
"""

from __future__ import annotations

import pytest
from chat_engine.admission import BreakerPolicy, Denied, InMemoryAdmission, Reservation
from chat_engine.admission.port import failure_category
from chat_engine.composition.runtime import build_runtime

from tests.admission.test_admission import Clock


async def _fail(adm: InMemoryAdmission, scope: str, category: str | None, n: int = 1) -> None:
    for _ in range(n):
        r = await adm.reserve([scope])
        assert isinstance(r, Reservation), r
        await adm.settle(r, success=False, category=category)


def test_policy_parsing_and_defaults() -> None:
    assert BreakerPolicy.from_env(None) == BreakerPolicy.legacy()
    assert BreakerPolicy.from_env("  ") == BreakerPolicy.legacy()
    p = BreakerPolicy.from_env(
        '{"timeout": 3, "invalid_credential": {"fails": 1, "open_seconds": 600}, "bad_request": 0}'
    )
    assert p.rule_for("timeout").fails == 3 and p.rule_for("timeout").open_seconds == 30.0
    assert p.rule_for("invalid_credential").open_seconds == 600
    assert p.rule_for("bad_request").counts is False
    assert p.rule_for("rate_limited") is None and p.rule_for(None) is None
    rec = BreakerPolicy.from_env("recommended")
    assert rec.rule_for("quota_exceeded").fails == 1 and rec.rule_for("context_window").fails == 0
    assert "categories" in rec.describe()
    with pytest.raises(ValueError):
        BreakerPolicy.parse("[1,2]")
    with pytest.raises(ValueError):
        BreakerPolicy.parse('{"timeout": true}')
    with pytest.raises(ValueError):
        BreakerPolicy.parse('{"timeout": -1}')


async def test_legacy_behaviour_unchanged_without_rules() -> None:
    clock = Clock()
    adm = InMemoryAdmission(clock=clock)
    await _fail(adm, "provider:p", "timeout", 4)
    assert isinstance(await adm.reserve(["provider:p"]), Reservation)
    await adm.settle(await adm.reserve(["provider:p"]), success=False, category="timeout")  # type: ignore[arg-type]
    # 4 + 1 (reserved ok) + 1 = 6 attempts, 5 failures → open
    d = await adm.reserve(["provider:p"])
    assert isinstance(d, Denied) and "breaker" in d.reason
    clock.tick(31)
    assert isinstance(await adm.reserve(["provider:p"]), Reservation)


async def test_category_rule_trips_after_n_and_holds_open_seconds() -> None:
    clock = Clock()
    adm = InMemoryAdmission(
        clock=clock,
        breaker_policy=BreakerPolicy.parse(
            '{"timeout": 2, "invalid_credential": {"fails": 1, "open_seconds": 600}}'
        ),
    )
    await _fail(adm, "provider:slow", "timeout", 1)
    assert isinstance(await adm.reserve(["provider:slow"]), Reservation)  # 1 < 2
    await _fail(adm, "provider:slow", "timeout", 1)
    d = await adm.reserve(["provider:slow"])
    assert isinstance(d, Denied) and d.retry_after_ms <= 30_000
    assert adm.breaker_open("provider:slow")
    snap = {s["scope"]: s for s in adm.snapshot()}
    assert (
        snap["provider:slow"]["breaker_open"]
        and snap["provider:slow"]["breaker_reason"] == "timeout"
    )
    # credential refusal: first hit opens for 10 minutes
    await _fail(adm, "provider:badkey", "invalid_credential", 1)
    d2 = await adm.reserve(["provider:badkey"])
    assert isinstance(d2, Denied) and d2.retry_after_ms > 500_000
    clock.tick(31)
    assert isinstance(await adm.reserve(["provider:slow"]), Reservation)
    assert isinstance(await adm.reserve(["provider:badkey"]), Denied)
    clock.tick(600)
    assert isinstance(await adm.reserve(["provider:badkey"]), Reservation)


async def test_zero_rule_never_counts_and_unruled_falls_back() -> None:
    clock = Clock()
    adm = InMemoryAdmission(
        clock=clock, breaker_policy=BreakerPolicy.parse('{"context_window": 0, "bad_request": 0}')
    )
    await _fail(adm, "provider:p", "context_window", 20)
    await _fail(adm, "provider:p", "bad_request", 20)
    assert isinstance(await adm.reserve(["provider:p"]), Reservation)
    assert not adm.breaker_open("provider:p")
    # a category without a rule still uses the relative breaker
    await _fail(adm, "provider:p", "retryable_server_error", 5)
    assert isinstance(await adm.reserve(["provider:p"]), Denied)
    snap = {s["scope"]: s for s in adm.snapshot()}
    assert snap["provider:p"]["breaker_reason"] == "relative"


async def test_category_window_expires() -> None:
    clock = Clock()
    adm = InMemoryAdmission(clock=clock, breaker_policy=BreakerPolicy.parse('{"timeout": 2}'))
    await _fail(adm, "provider:p", "timeout", 1)
    clock.tick(61)  # first failure aged out of the 60s category window
    await _fail(adm, "provider:p", "timeout", 1)
    assert isinstance(await adm.reserve(["provider:p"]), Reservation)


async def test_tenant_scope_still_exempt_with_rules() -> None:
    adm = InMemoryAdmission(breaker_policy=BreakerPolicy.recommended())
    for i in range(3):
        r = await adm.reserve(["tenant:t", f"provider:p{i}"])
        assert isinstance(r, Reservation)
        await adm.settle(r, success=False, category="invalid_credential")
    assert isinstance(await adm.reserve(["tenant:t", "provider:fresh"]), Reservation)
    for i in range(3):
        assert isinstance(await adm.reserve(["tenant:t", f"provider:p{i}"]), Denied)


def test_runtime_wires_policy_from_env_and_admin_exposes_it() -> None:
    from chat_engine.api.app import create_app
    from fastapi.testclient import TestClient

    from tests.conftest import HERMETIC_ENV

    rt = build_runtime({**HERMETIC_ENV, "CE_BREAKER_POLICY_JSON": '{"timeout": 3}'})
    adm = rt.extensions["admission"]
    assert adm.policy.rule_for("timeout").fails == 3
    body = TestClient(create_app(rt)).get("/v1/admin/admission").json()
    assert body["breaker_policy"]["categories"] == {"timeout": {"fails": 3, "open_seconds": 30.0}}
    legacy = build_runtime(HERMETIC_ENV).extensions["admission"]
    assert legacy.policy == BreakerPolicy.legacy()


def test_failure_category_helper() -> None:
    from types import SimpleNamespace as NS

    err = NS(category=NS(value="timeout"))
    report = NS(nodes=[NS(response=NS(error=err), node=None)])
    assert failure_category(report) == "timeout"
    report2 = NS(nodes=[NS(response=None, node=NS(error={"category": "rate_limited"}))])
    assert failure_category(report2) == "rate_limited"
    assert failure_category(NS(nodes=[])) is None
    assert failure_category(NS(nodes=[NS(response=NS(error=None), node=NS(error=None))])) is None


async def test_orchestrator_skips_provider_with_open_breaker(runtime) -> None:
    """CE-24/B replaces the orchestrator's private 120s skip list with the admission
    breaker: once a provider scope is open, derived tiers exclude that provider."""
    from tests.orchestration.test_strategies import add_scripted

    add_scripted(runtime, "cheap", ["m-cheap"], {"m-cheap": "hi"})
    add_scripted(runtime, "pricey", ["m-pricey"], {"m-pricey": "hi"})
    orch = runtime.extensions["orchestrator"]
    before = orch._cheapest_first(4)
    assert "m-cheap" in before and "m-pricey" in before
    adm = runtime.extensions["admission"]
    adm.policy = BreakerPolicy.parse('{"quota_exceeded": 1}')
    await _fail(adm, "provider:cheap", "quota_exceeded", 1)
    assert orch._recently_failed("cheap") and not orch._recently_failed("pricey")
    after = orch._cheapest_first(4)
    assert "m-cheap" not in after and "m-pricey" in after
    # with a rule configured, the legacy private skip list is no longer populated
    orch._note_failure("pricey", "quota_exceeded")
    assert "pricey" not in orch._recent_failures
