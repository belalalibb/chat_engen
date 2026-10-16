"""CE-14: 3-stage plan → artifacts with fakes; approval pause/resume; scaffold extraction;
path-traversal safety; upstream generation_deferred untouched."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from chat_engine.app_factory import (
    GenerateRequest,
    InMemoryArtifactStore,
    JobStatus,
    LocalArtifactStore,
)

from tests.orchestration.test_strategies import add_scripted

SCAFFOLD = json.dumps({"README.md": "# demo", "src/app.py": "print('hi')", "../evil": "x"})


def _wait(client, job_id, want=("succeeded", "failed", "awaiting_approval", "cancelled"), tries=60):
    for _ in range(tries):
        j = client.get(f"/v1/apps/{job_id}").json()
        if j["status"] in want:
            return j
        import time

        time.sleep(0.05)
    raise AssertionError(f"job stuck: {j['status']}")


def test_three_stage_plan_with_approval(client, runtime) -> None:
    add_scripted(runtime, "pf", ["factory-m"], {"factory-m": SCAFFOLD})
    r = client.post(
        "/v1/apps:generate", json={"name": "Todo", "brief": "a todo app", "model": "factory-m"}
    )
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["status"] in ("queued", "running") and job["status_url"].endswith(job["id"])
    j = _wait(client, job["id"])
    assert j["status"] == "awaiting_approval" and j["current_stage"] == 1
    assert (
        j["stages"][0]["status"] == "succeeded" and j["stages"][0]["artifact"]["name"] == "SPEC.md"
    )
    assert j["stages"][1]["status"] == "awaiting_approval"
    # cannot approve twice / wrong state later; approve now
    r = client.post(f"/v1/apps/{job['id']}:approve", json={"approved_by": "owner"})
    assert r.status_code == 200, r.text
    j = _wait(client, job["id"], want=("succeeded", "failed"))
    assert j["status"] == "succeeded", j
    assert [s["status"] for s in j["stages"]] == ["succeeded"] * 3
    assert j["stages"][1]["approved_by"] == "owner"
    names = [a["name"] for a in j["artifacts"]]
    assert {
        "SPEC.md",
        "scaffold.json",
        "REVIEW.md",
        "scaffold/README.md",
        "scaffold/src/app.py",
    } <= set(names)
    assert not any("evil" in n for n in names)  # traversal rejected, never written
    assert j["stages"][1]["extracted_files"] == 2
    art = client.get(f"/v1/apps/{job['id']}/artifacts/scaffold/src/app.py")
    assert art.status_code == 200 and art.text == "print('hi')" and art.headers["x-artifact-sha256"]
    assert client.get(f"/v1/apps/{job['id']}/artifacts/nope").status_code == 404
    assert client.post(f"/v1/apps/{job['id']}:approve").status_code == 409
    assert client.get("/v1/apps").json()["total"] == 1
    assert j["usage"]["total_tokens"] > 0
    # usage recorded for the 3 stages
    recs = client.get("/v1/usage/records").json()["records"]
    assert len([x for x in recs if x["surface"] == "orchestration"]) == 3


def test_auto_approve_and_custom_stages(client, runtime) -> None:
    add_scripted(runtime, "pf", ["factory-m"], {"factory-m": "ok"})
    r = client.post(
        "/v1/apps:generate",
        json={
            "name": "X",
            "brief": "b",
            "model": "factory-m",
            "auto_approve": True,
            "stages": [
                {"key": "spec", "instructions": "spec it", "output_name": "SPEC.md"},
                {"key": "review", "instructions": "review it", "requires_approval": True},
            ],
        },
    )
    j = _wait(client, r.json()["id"], want=("succeeded", "failed"))
    assert j["status"] == "succeeded" and [a["name"] for a in j["artifacts"]] == [
        "SPEC.md",
        "review.md",
    ]


def test_failed_stage_marks_job_failed(client, runtime) -> None:
    add_scripted(runtime, "pbroken", ["broken-m"], {}, fail={"broken-m"})
    r = client.post(
        "/v1/apps:generate",
        json={"name": "X", "brief": "b", "model": "broken-m", "auto_approve": True},
    )
    j = _wait(client, r.json()["id"], want=("succeeded", "failed"))
    assert (
        j["status"] == "failed"
        and j["stages"][0]["status"] == "failed"
        and j["error"].split(":")[0] in ("execution_failed", "not_found")
    )
    assert client.get(f"/v1/apps/{uuid4()}").status_code == 404


def test_auto_model_routing(client) -> None:
    r = client.post("/v1/apps:generate", json={"name": "X", "brief": "b", "auto_approve": True})
    j = _wait(client, r.json()["id"], want=("succeeded", "failed"))
    assert j["status"] == "succeeded" and j["stages"][0]["provider_key"] == "local_echo"


def test_previous_outputs_travel_in_packet(client, runtime) -> None:
    a = add_scripted(runtime, "pf", ["factory-m"], {"factory-m": "STAGE-OUT"})
    r = client.post(
        "/v1/apps:generate",
        json={"name": "X", "brief": "the brief", "model": "factory-m", "auto_approve": True},
    )
    _wait(client, r.json()["id"], want=("succeeded", "failed"))
    assert len(a.calls) == 3
    last = a.calls[2]["messages"]
    assistant = [m["content"] for m in last if m["role"] == "assistant"]
    assert len(assistant) == 2 and assistant[0].startswith("[spec]") and "STAGE-OUT" in assistant[1]
    assert "the brief" in last[1]["content"]


def test_artifact_stores(tmp_path) -> None:
    for store in (InMemoryArtifactStore(), LocalArtifactStore(tmp_path / "art")):
        jid = uuid4()
        ref = store.put(jid, "a/b.txt", b"hello", stage="s")
        assert (
            ref.ref == f"artifact://{jid}/a/b.txt"
            and ref.size == 5
            and store.get(ref.ref) == b"hello"
        )
        assert [r.name for r in store.list(jid)] == ["a/b.txt"]
        with pytest.raises(ValueError):
            store.put(jid, "../x", b"", stage="s")
        with pytest.raises(ValueError):
            store.put(jid, "/etc/passwd", b"", stage="s")


def test_upstream_generation_deferred_untouched() -> None:
    import inspect

    from core.agent import app_factory

    src = inspect.getsource(app_factory)
    assert "generation_deferred" in src


def test_feature_flag_off() -> None:
    from chat_engine.api.app import create_app
    from chat_engine.composition.runtime import build_runtime
    from fastapi.testclient import TestClient

    env = {"CE_FEATURE_APP_FACTORY": "0"}
    c = TestClient(create_app(build_runtime(env), environ=env))
    assert c.post("/v1/apps:generate", json={"name": "x", "brief": "b"}).status_code in (404, 405)


def test_cancel_running_job(runtime) -> None:
    async def run() -> None:
        runner = runtime.extensions["app_factory"]
        job = runner.submit(GenerateRequest(name="x", brief="b", auto_approve=True))
        job = runner.cancel(job.id)
        assert job.status is JobStatus.CANCELLED
        await asyncio.sleep(0)

    asyncio.run(run())
