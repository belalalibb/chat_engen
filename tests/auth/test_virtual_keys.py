"""CE-24/D virtual keys + CE-24/L rpd/tpd.

* CE_AUTH_MODE=off (default): no middleware, no /v1/admin/keys, anonymous as before;
* optional: keys resolve when sent, anonymous still allowed;
* required: anonymous /v1/* → 401 unauthenticated; /healthz stays open;
* master key manages keys; secret is shown once; only sha256 kept (prefix for display);
* models_allowed / project_ids → 403 capability_denied; budget → 403 entitlement_exceeded;
* key:<id> admission scope (rpm) → 429 admission_denied; spend accrues per key;
* rpd/tpd: rolling 24h window denies with Dim.RPD / Dim.TPD.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from chat_engine.admission import Denied, InMemoryAdmission, Limits, Reservation
from chat_engine.api.app import create_app
from chat_engine.auth import AuthMode, KeyCreate, KeyService
from chat_engine.composition.runtime import build_runtime
from tests.admission.test_admission import Clock
from tests.conftest import HERMETIC_ENV
from tests.orchestration.test_strategies import add_scripted

MASTER = "ce_live_test-master-key-0000000000000000"


def _app(mode: str, **env):
    rt = build_runtime({**HERMETIC_ENV, "CE_AUTH_MODE": mode, "CE_MASTER_KEY": MASTER, **env})
    add_scripted(rt, "pa", ["m-a"], {"m-a": "answer a"})
    add_scripted(rt, "pb", ["m-b"], {"m-b": "answer b"})
    return rt, TestClient(create_app(rt, environ={"CE_AUTH_MODE": mode}))


def _h(key: str) -> dict[str, str]:
    return {"authorization": f"Bearer {key}"}


# --- modes ----------------------------------------------------------------------------------------


def test_auth_off_is_default_and_invisible(client) -> None:
    assert client.get("/healthz").json()["auth_mode"] == "off"
    assert client.get("/v1/admin/keys").status_code == 404
    assert client.get("/v1/auth/whoami").status_code == 404
    # a stray Authorization header is ignored
    r = client.post("/v1/chat", json={"message": "hi"}, headers=_h("ce_live_whatever"))
    assert r.status_code == 200


def test_required_mode_blocks_anonymous_but_not_health() -> None:
    _rt, c = _app("required")
    assert c.get("/healthz").status_code == 200
    r = c.post("/v1/chat", json={"message": "hi"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated"
    assert r.json()["error"]["details"]["reason"] == "missing_key"
    assert c.get("/v1/catalog/models").status_code == 401
    r2 = c.post("/v1/chat", json={"message": "hi"}, headers=_h("ce_live_nope"))
    assert r2.status_code == 401 and r2.json()["error"]["details"]["reason"] == "unknown_key"
    ok = c.post("/v1/chat", json={"message": "hi", "model": "m-a"}, headers=_h(MASTER))
    assert ok.status_code == 200, ok.text


def test_optional_mode_allows_anonymous_and_resolves_keys() -> None:
    _rt, c = _app("optional")
    assert c.post("/v1/chat", json={"message": "hi"}).status_code == 200
    who = c.get("/v1/auth/whoami").json()
    assert who["anonymous"] is True and who["auth_mode"] == "optional"
    me = c.get("/v1/auth/whoami", headers=_h(MASTER)).json()
    assert me["anonymous"] is False and me["is_master"] is True


def test_bad_mode_value_is_rejected() -> None:
    import pytest

    with pytest.raises(ValueError):
        AuthMode.from_env("sometimes")


# --- key management -------------------------------------------------------------------------------


def test_master_manages_keys_and_secret_shown_once() -> None:
    rt, c = _app("optional")
    # non-master cannot manage
    assert c.get("/v1/admin/keys").status_code == 401
    r = c.post(
        "/v1/admin/keys",
        json={"name": "team-a", "models_allowed": ["m-a"], "rpm": 2, "budget_usd": 1.0},
        headers=_h(MASTER),
    )
    assert r.status_code == 201, r.text
    created = r.json()
    key = created["key"]
    rec = created["record"]
    assert key.startswith("ce_live_") and rec["prefix"].startswith("ce_live_")
    assert "key" not in rec and rec["models_allowed"] == ["m-a"] and rec["rpm"] == 2
    listing = c.get("/v1/admin/keys", headers=_h(MASTER)).json()
    assert listing["total"] == 2 and all("key" not in k for k in listing["keys"])
    # the secret is not recoverable from the service
    svc: KeyService = rt.extensions["keys"]
    assert key not in repr(svc.list())
    # the key may read itself but not others / not manage
    own = c.get(f"/v1/admin/keys/{rec['key_id']}", headers=_h(key))
    assert own.status_code == 200 and own.json()["name"] == "team-a"
    assert c.get("/v1/admin/keys", headers=_h(key)).status_code == 403
    assert c.get(f"/v1/admin/keys/{svc.master_key_id}", headers=_h(key)).status_code == 403
    # patch + disable
    p = c.patch(
        f"/v1/admin/keys/{rec['key_id']}", json={"disabled": True}, headers=_h(MASTER)
    )
    assert p.status_code == 200 and p.json()["disabled"] is True
    r2 = c.post("/v1/chat", json={"message": "hi", "model": "m-a"}, headers=_h(key))
    assert r2.status_code == 403 and r2.json()["error"]["details"]["reason"] == "key_disabled"
    # delete; master cannot be deleted
    assert c.delete(f"/v1/admin/keys/{rec['key_id']}", headers=_h(MASTER)).status_code == 204
    assert c.get(f"/v1/admin/keys/{rec['key_id']}", headers=_h(MASTER)).status_code == 404
    assert c.delete(f"/v1/admin/keys/{svc.master_key_id}", headers=_h(MASTER)).status_code == 403


def test_model_allowlist_rpm_and_spend() -> None:
    rt, c = _app("optional")
    r = c.post(
        "/v1/admin/keys",
        json={"name": "limited", "models_allowed": ["m-a"], "rpm": 2},
        headers=_h(MASTER),
    )
    key = r.json()["key"]
    kid = r.json()["record"]["key_id"]
    # disallowed model → 403 capability_denied
    bad = c.post("/v1/chat", json={"message": "hi", "model": "m-b"}, headers=_h(key))
    assert bad.status_code == 403, bad.text
    assert bad.json()["error"]["code"] == "capability_denied"
    assert bad.json()["error"]["details"]["reason"] == "model_not_allowed"
    # allowed model works twice, third hits the key's rpm
    for _ in range(2):
        ok = c.post("/v1/chat", json={"message": "hi", "model": "m-a"}, headers=_h(key))
        assert ok.status_code == 200, ok.text
    third = c.post("/v1/chat", json={"message": "hi", "model": "m-a"}, headers=_h(key))
    assert third.status_code == 429 and third.json()["error"]["code"] == "admission_denied"
    assert third.json()["error"]["details"]["scope"] == f"key:{kid}"
    # anonymous caller unaffected by the key's rpm
    assert c.post("/v1/chat", json={"message": "hi", "model": "m-a"}).status_code == 200
    # spend accrued on the key (2 successful requests)
    rec = c.get(f"/v1/admin/keys/{kid}", headers=_h(MASTER)).json()
    assert rec["spend"]["requests"] == 2 and rec["spend"]["last_used_at"]
    assert rec["spend"]["tokens_in"] + rec["spend"]["tokens_out"] > 0
    records = c.get("/v1/usage/records", params={"limit": 100}).json()["records"]
    assert sum(1 for x in records if x["metadata"].get("key_id") == kid) == 2
    # admission snapshot shows the key scope
    snap = {s["scope"]: s for s in rt.extensions["admission"].snapshot()}
    assert snap[f"key:{kid}"]["limits"]["rpm"] == 2


def test_budget_and_expiry() -> None:
    rt, c = _app("optional")
    svc: KeyService = rt.extensions["keys"]
    r = c.post("/v1/admin/keys", json={"name": "b", "budget_usd": 0.0001}, headers=_h(MASTER))
    key, kid = r.json()["key"], r.json()["record"]["key_id"]
    from uuid import UUID

    svc.record_spend(UUID(kid), tokens_in=1, tokens_out=1, cost_usd=0.001)
    r2 = c.post("/v1/chat", json={"message": "hi", "model": "m-a"}, headers=_h(key))
    assert r2.status_code == 403 and r2.json()["error"]["code"] == "entitlement_exceeded"
    assert r2.json()["error"]["details"]["reason"] == "budget_exhausted"
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    r3 = c.post("/v1/admin/keys", json={"name": "old", "expires_at": past}, headers=_h(MASTER))
    r4 = c.post("/v1/chat", json={"message": "hi"}, headers=_h(r3.json()["key"]))
    assert r4.status_code == 403 and r4.json()["error"]["details"]["reason"] == "key_expired"


def test_master_key_generated_when_unset() -> None:
    svc = KeyService(tenant_id=HERMETIC_ENV and __import__("uuid").uuid4(), mode=AuthMode.OPTIONAL)
    assert svc.master_generated and svc.master_key_id is not None
    off = KeyService(tenant_id=__import__("uuid").uuid4(), mode=AuthMode.OFF)
    assert not off.master_generated and off.list() == []
    created = svc.create(KeyCreate(name="x"))
    assert svc.resolve(created.key).key_id == created.record.key_id
    assert svc.resolve(None) is None


# --- CE-24/L rpd / tpd ----------------------------------------------------------------------------


async def test_rpd_and_tpd_rolling_day() -> None:
    clock = Clock()
    adm = InMemoryAdmission(clock=clock)
    adm.set_limits("key:k", Limits(rpm=1000, concurrency=100, rpd=2, tpd=1000, source="test"))
    r1 = await adm.reserve(["key:k"], tokens_estimate=400)
    r2 = await adm.reserve(["key:k"], tokens_estimate=400)
    assert isinstance(r1, Reservation) and isinstance(r2, Reservation)
    d = await adm.reserve(["key:k"], tokens_estimate=1)
    assert isinstance(d, Denied) and d.dimension.value == "rpd" and d.retry_after_ms > 0
    await adm.settle(r1, tokens_actual=400)
    await adm.settle(r2, tokens_actual=400)
    clock.tick(3600)  # one hour later: rpd still exhausted (rolling 24h)
    assert isinstance(await adm.reserve(["key:k"]), Denied)
    clock.tick(86_400)  # a day later: window rolled
    r3 = await adm.reserve(["key:k"], tokens_estimate=900)
    assert isinstance(r3, Reservation)
    await adm.settle(r3, tokens_actual=900)
    d2 = await adm.reserve(["key:k"], tokens_estimate=200)
    assert isinstance(d2, Denied) and d2.dimension.value == "tpd"
    snap = {s["scope"]: s for s in adm.snapshot()}
    assert snap["key:k"]["rpd_used"] == 1 and snap["key:k"]["tpd_used"] == 900
    # scopes without rpd/tpd report None and keep no day window
    adm.set_limits("provider:p", Limits(rpm=10, concurrency=4, source="test"))
    await adm.reserve(["provider:p"])
    snap = {s["scope"]: s for s in adm.snapshot()}
    assert snap["provider:p"]["rpd_used"] is None and "provider:p" not in adm._days


def test_key_with_rpd_sets_admission_limits() -> None:
    rt, c = _app("optional")
    r = c.post("/v1/admin/keys", json={"name": "daily", "rpd": 1}, headers=_h(MASTER))
    key = r.json()["key"]
    assert c.post("/v1/chat", json={"message": "hi", "model": "m-a"}, headers=_h(key)).status_code == 200
    d = c.post("/v1/chat", json={"message": "hi", "model": "m-a"}, headers=_h(key))
    assert d.status_code == 429 and d.json()["error"]["details"]["dimension"] == "rpd"
