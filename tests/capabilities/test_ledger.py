"""CE-06 D6: declared ∧ verified ∧ enabled ∧ available_now — four states × probes."""

from __future__ import annotations

from datetime import timedelta

from chat_engine.capabilities import CapabilityLedger, VerificationStatus
from core.contracts.provider import ProviderCapabilities


async def _passing(_p: str, _m: str | None):
    return True, None, 3, {"ok": True}


async def _failing(_p: str, _m: str | None):
    return False, "nope", 3, {}


def test_manifest_declared_but_unverified_is_refused() -> None:
    led = CapabilityLedger()
    led.declare_from_manifest("p", ProviderCapabilities(chat=True))
    assert led.gate("p", "chat").allowed is False
    assert led.gate("p", "chat").reason == "unverified (no passing probe)"
    assert led.gate("p", "vision_input").reason == "not declared"
    assert (
        led.gate("p", "streaming_text").reason == "not declared"
    )  # extra caps need explicit declaration


async def test_probe_pass_auto_enables_and_fail_isolates() -> None:
    led = CapabilityLedger()
    led.declare_from_manifest("p", ProviderCapabilities(chat=True))
    led.register_probe("chat", _passing)
    r = await led.probe("p", "chat")
    assert r.passed and led.gate("p", "chat").allowed
    led.register_probe("chat", _failing)
    await led.probe("p", "chat")
    g = led.gate("p", "chat")
    assert not g.allowed and g.reason == "probe failed"


async def test_admin_disable_wins_and_reenable_requires_passed() -> None:
    led = CapabilityLedger()
    led.declare_from_manifest("p", ProviderCapabilities(chat=True))
    led.register_probe("chat", _passing)
    await led.probe("p", "chat")
    led.set_enabled("p", "chat", False)
    assert led.gate("p", "chat").reason == "disabled"
    led.set_enabled("p", "chat", True)
    assert led.gate("p", "chat").allowed


async def test_stale_after_ttl_still_serves_but_is_marked() -> None:
    led = CapabilityLedger(ttl=timedelta(seconds=0))
    led.declare_from_manifest("p", ProviderCapabilities(chat=True))
    led.register_probe("chat", _passing)
    await led.probe("p", "chat")
    e = led.get("p", "chat")
    assert e is not None and e.verified.status is VerificationStatus.STALE and e.executable


async def test_crashing_probe_is_failed_not_exception() -> None:
    led = CapabilityLedger()
    led.declare("p", "json_mode", True, source="admin")

    async def boom(_p: str, _m: str | None):
        raise RuntimeError("x")

    led.register_probe("json_mode", boom)
    r = await led.probe("p", "json_mode")
    assert r.passed is False and "raised" in (r.detail or "")


def test_unavailable_now_blocks() -> None:
    led = CapabilityLedger()
    led.declare("p", "chat", True, source="manifest")
    led.record_external_verification("p", "chat", passed=True, detail="x")
    assert led.gate("p", "chat").allowed
    led.set_available("p", False)
    assert led.gate("p", "chat").reason == "unavailable now"


def test_execute_gate_via_api(client, runtime) -> None:
    # 'reasoning' is a Model capability upstream (router admits it) — but the
    # ledger has no passing probe for it on local_echo ⇒ refused with evidence.
    denied = client.post(
        "/v1/execute", json={"ask": "hi", "requirements": {"capabilities": ["reasoning"]}}
    )
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "capability_denied"
    assert denied.json()["error"]["details"]["ce_code"] == "unsupported_capability"
    rows = client.get(
        "/v1/admin/capabilities/ledger?provider_key=local_echo&executable_only=true"
    ).json()
    assert [e["capability"] for e in rows["entries"]] == ["chat"]
    probe = client.post("/v1/admin/capabilities/local_echo/multi_turn:probe")
    assert probe.status_code == 200 and probe.json()["passed"] is True
    assert (
        client.get(
            "/v1/admin/capabilities/ledger?provider_key=local_echo&executable_only=true"
        ).json()["total"]
        == 2
    )
    # after admin declares+verifies reasoning, the same request passes
    runtime.extensions["capabilities"].declare("local_echo", "reasoning", True, source="admin")
    runtime.extensions["capabilities"].record_external_verification(
        "local_echo", "reasoning", passed=True, detail="test"
    )
    ok = client.post(
        "/v1/execute", json={"ask": "hi", "requirements": {"capabilities": ["reasoning"]}}
    )
    assert ok.status_code == 200, ok.text
