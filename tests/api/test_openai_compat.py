"""CE-10: OpenAI-compatible surface, hermetic.

Raw HTTP tests always run; the official ``openai`` SDK tests run when the SDK is
installed (``pip install -e .[test-openai]``) and are skipped otherwise.
"""

from __future__ import annotations

import json

import httpx
import pytest
from chat_engine.api.app import create_app
from chat_engine.composition.runtime import Runtime, build_runtime
from fastapi.testclient import TestClient
from tests.streaming.test_five_cases import add_streaming_provider



def _chunks(resp: httpx.Response) -> list[dict]:
    out = []
    for line in resp.text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            out.append(json.loads(line[6:]))
    assert resp.text.rstrip().endswith("data: [DONE]")
    return out


def test_sync_completion_shape(client) -> None:
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "local-echo-1",
            "messages": [
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "hello there"},
            ],
            "temperature": 0.2,
            "max_tokens": 50,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "chat.completion" and body["id"].startswith("chatcmpl-")
    assert body["model"] == "local-echo-1"
    ch = body["choices"][0]
    assert ch["index"] == 0 and ch["finish_reason"] == "stop"
    assert ch["message"]["role"] == "assistant" and isinstance(ch["message"]["content"], str)
    assert set(body["usage"]) == {"prompt_tokens", "completion_tokens", "total_tokens"}
    assert body["chat_engine"]["provider_key"] == "local_echo"
    assert body["chat_engine"]["execution_id"]
    # the execution is visible on the upstream-compatible surface too
    assert client.get(f"/v1/executions/{body['chat_engine']['execution_id']}").status_code == 200


def test_content_parts_and_unknown_model(client) -> None:
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "local-echo-1",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "parts ok"}]}],
        },
    )
    assert r.status_code == 200 and "parts ok" in r.json()["choices"][0]["message"]["content"]
    r = client.post(
        "/v1/chat/completions",
        json={"model": "does-not-exist", "messages": [{"role": "user", "content": "x"}]},
    )
    assert r.status_code in (404, 503), r.text
    err = r.json()["error"]
    assert err["type"] in ("not_found_error", "api_error") and err["code"] and "message" in err


def test_validation_error_is_openai_shaped_or_upstream(client) -> None:
    r = client.post("/v1/chat/completions", json={"model": "local-echo-1", "messages": []})
    assert r.status_code == 422
    assert "error" in r.json()


def test_stream_chunks(client, runtime: Runtime) -> None:
    add_streaming_provider(runtime, "sp10")
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "s-model",
            "messages": [{"role": "user", "content": "alpha beta gamma"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        },
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    chunks = _chunks(r)
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert chunks[0]["choices"][0]["delta"].get("role") == "assistant"
    text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks)
    assert text == "alpha beta gamma"
    last = chunks[-1]
    assert last["choices"][0]["finish_reason"] == "stop" and "usage" in last
    assert last["chat_engine"]["provider_key"] == "sp10"
    assert len({c["id"] for c in chunks}) == 1


def test_stream_failure_after_first_delta_surfaces_error_chunk(client, runtime: Runtime) -> None:
    adapter = add_streaming_provider(runtime, "flaky10", model="flaky-model")
    adapter.fail_after_words = 1
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "flaky-model",
            "messages": [{"role": "user", "content": "one two three"}],
            "stream": True,
        },
    )
    chunks = _chunks(r)
    last = chunks[-1]
    assert last["choices"][0]["finish_reason"] == "error"
    assert last["error"]["code"] == "stream_interrupted" and last["chat_engine"]["resumable"]


def test_openai_models_list(client) -> None:
    r = client.get("/v1/openai/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list" and body["data"]
    ids = [m["id"] for m in body["data"]]
    assert "local-echo-1" in ids and ids == sorted(ids)
    assert all(m["object"] == "model" for m in body["data"])
    assert client.get("/v1/openai/models/local-echo-1").json()["id"] == "local-echo-1"
    assert client.get("/v1/openai/models/nope").status_code == 404


def test_feature_flag_disables_surface() -> None:
    rt = build_runtime({"CE_FEATURE_OPENAI_COMPAT": "0"})
    c = TestClient(create_app(rt, environ={"CE_FEATURE_OPENAI_COMPAT": "0"}))
    r = c.post(
        "/v1/chat/completions",
        json={"model": "local-echo-1", "messages": [{"role": "user", "content": "x"}]},
    )
    assert r.status_code == 404
    assert c.post("/v1/chat", json={"message": "still works"}).status_code == 200


# --- official SDK ------------------------------------------------------------------------------


@pytest.fixture
def sdk(client):
    sdk = pytest.importorskip("openai")
    return sdk.OpenAI(
        base_url=str(client.base_url) + "/v1",
        api_key="fakekey-sdk-0001",
        http_client=httpx.Client(transport=httpx.ASGITransport(app=client.app), base_url=client.base_url),
    )


def test_sdk_sync(sdk) -> None:
    r = sdk.chat.completions.create(
        model="local-echo-1", messages=[{"role": "user", "content": "sdk hello"}]
    )
    assert r.choices[0].message.role == "assistant"
    assert isinstance(r.choices[0].message.content, str)
    assert r.usage is not None and r.model == "local-echo-1"


def test_sdk_stream(sdk, runtime: Runtime) -> None:
    add_streaming_provider(runtime, "sp10")
    parts = []
    for ev in sdk.chat.completions.create(
        model="s-model", messages=[{"role": "user", "content": "sdk streams fine"}], stream=True
    ):
        parts.append(ev.choices[0].delta.content or "")
    assert "".join(parts) == "sdk streams fine"


def test_sdk_errors(sdk) -> None:
    import openai

    with pytest.raises((openai.NotFoundError, openai.APIStatusError)) as ei:
        sdk.chat.completions.create(model="ghost", messages=[{"role": "user", "content": "x"}])
    assert ei.value.status_code in (404, 503)
    assert ei.value.body and ei.value.body.get("code")
