"""CE-05 end-to-end: BYOK key → draft → validate → verify → enable → routed
execution via /v1/execute → disable → rollback. Secret never leaks anywhere."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from chat_engine.api.app import create_app
from chat_engine.composition.runtime import build_runtime
from chat_engine.providers.lifecycle import ProviderLifecycleService
from tests.providers.fake_openai import FakeOpenAI

SECRET = "sk-test-valid-key-0001"


@pytest.fixture
def world():
    fake = FakeOpenAI(models=["fake-chat-1"])
    rt = build_runtime({})
    # inject the hermetic transport for every managed provider
    rt.extensions["lifecycle"] = ProviderLifecycleService(rt, transport_factory=lambda _cfg: fake.transport())
    client = TestClient(create_app(rt))
    return fake, rt, client


def test_full_lifecycle_with_byok(world) -> None:
    fake, rt, client = world

    # 1) BYOK: key enters once, only a ref comes back
    cred = client.post("/v1/credentials", json={"secret": SECRET, "label": "my key", "provider_hint": "fake"})
    assert cred.status_code == 201, cred.text
    body = cred.json()
    assert SECRET not in cred.text and body["last4"] == SECRET[-4:]
    ref = body["credential_ref"]

    # 2) draft
    d = client.post("/v1/admin/providers/myfake", json={"display_name": "My Fake", "base_url": "https://fake.local/v1", "credential_ref": ref})
    assert d.status_code == 201 and d.json()["state"] == "draft" and d.json()["revision"] == 1
    dup = client.post("/v1/admin/providers/myfake", json={"display_name": "x", "base_url": "https://x"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "conflict"

    # 3) validate → discovered
    v = client.post("/v1/admin/providers/myfake:validate")
    assert v.status_code == 200, v.text
    assert v.json()["state"] == "discovered" and v.json()["discovered_models"] == ["fake-chat-1"]

    # 4) enable before verify is refused loudly
    early = client.post("/v1/admin/providers/myfake:enable")
    assert early.status_code == 409 and early.json()["error"]["details"]["current"] == "discovered"

    # 5) verify (one real probe) → verified
    ok = client.post("/v1/admin/providers/myfake:verify")
    assert ok.status_code == 200 and ok.json()["state"] == "verified", ok.text
    assert fake.calls[-1]["max_tokens"] == 8

    # 6) enable → registered + routable
    en = client.post("/v1/admin/providers/myfake:enable")
    assert en.status_code == 200 and en.json()["state"] == "enabled"
    assert "myfake" in client.get("/healthz").json()["providers"]
    models = {m["name"]: m for m in client.get("/v1/models").json()["models"]}
    assert models["fake-chat-1"]["providers"] == ["myfake"]

    # 7) a real routed execution through the ONE Router + ExecutionService
    r = client.post("/v1/execute", json={"ask": "hello there", "model_policy": {"type": "explicit_model", "model_id": "fake-chat-1"}})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["content"] == "echo:hello there"
    assert fake.calls[-1]["messages"][-1] == {"role": "user", "content": "hello there"}

    # 8) catalog shows it once, with a models.dev-free identity row
    cat = client.get("/v1/catalog/models/fake-chat-1").json()
    assert cat["configured_provider_count"] == 1 and cat["providers"][0]["provider_key"] == "myfake"

    # 9) disable → unroutable; execution fails over to nothing ⇒ model_unavailable
    dis = client.post("/v1/admin/providers/myfake:disable", json={"reason": "maintenance"})
    assert dis.json()["state"] == "disabled"
    assert "myfake" not in client.get("/healthz").json()["providers"]
    gone = client.post("/v1/execute", json={"ask": "x", "model_policy": {"type": "explicit_model", "model_id": "fake-chat-1"}})
    assert gone.status_code == 503 and gone.json()["error"]["code"] == "model_unavailable"

    # 10) revisions are append-only; rollback to rev 1 creates a NEW draft revision
    revs = client.get("/v1/admin/providers/myfake/revisions").json()["revisions"]
    states = [x["state"] for x in revs]
    assert states == ["draft", "validating", "discovered", "verified", "enabled", "disabled"]
    rb = client.post("/v1/admin/providers/myfake:rollback", json={"revision": 1})
    assert rb.status_code == 200 and rb.json()["state"] == "draft" and rb.json()["revision"] == 7
    assert rb.json()["config_hash"] == revs[0]["config_hash"]

    # 11) the secret never appeared in any response or stored revision
    everything = json.dumps(client.get("/v1/admin/providers/myfake/revisions").json()) + json.dumps(client.get("/v1/credentials").json()) + json.dumps(client.get("/v1/admin/providers").json())
    assert SECRET not in everything
    # and the provider only ever saw it as a bearer header, never anything else
    assert all(a == f"Bearer {SECRET}" for a in fake.seen_auth)


def test_validate_with_wrong_key_fails_and_can_retry(world) -> None:
    fake, rt, client = world
    bad = client.post("/v1/credentials", json={"secret": "sk-test-wrong-key-00000", "label": "bad"}).json()["credential_ref"]
    client.post("/v1/admin/providers/p2", json={"display_name": "P2", "base_url": "https://fake.local/v1", "credential_ref": bad})
    v = client.post("/v1/admin/providers/p2:validate").json()
    assert v["state"] == "failed" and "invalid" in v["reason"]
    good = client.post("/v1/credentials", json={"secret": SECRET, "label": "good"}).json()["credential_ref"]
    up = client.put("/v1/admin/providers/p2", json={"display_name": "P2", "base_url": "https://fake.local/v1", "credential_ref": good})
    assert up.status_code == 200 and up.json()["state"] == "draft"
    assert client.post("/v1/admin/providers/p2:validate").json()["state"] == "discovered"


def test_revoked_credential_fails_validation(world) -> None:
    fake, rt, client = world
    c = client.post("/v1/credentials", json={"secret": SECRET, "label": "k"}).json()
    client.post("/v1/admin/providers/p3", json={"display_name": "P3", "base_url": "https://fake.local/v1", "credential_ref": c["credential_ref"]})
    assert client.delete(f"/v1/credentials/{c['id']}").json()["state"] == "revoked"
    v = client.post("/v1/admin/providers/p3:validate").json()
    assert v["state"] == "failed" and "does not resolve" in v["reason"]
