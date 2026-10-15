"""CE-03: /v1/execute carries payload["messages"] alongside ask/context (additive);
goldens still replay because the hermetic adapter output is unchanged."""

from __future__ import annotations

import json
from uuid import UUID


def test_execute_payload_has_messages_and_golden_unchanged(client, runtime) -> None:
    r = client.post("/v1/execute", json={"ask": "hello"})
    assert r.status_code == 200
    body = r.json()
    content = json.loads(body["result"]["content"])
    assert content["echo"] == "hello" and content["provider"] == "local-echo"
    report = runtime.executions.get(runtime.tenant_id, UUID(body["execution_id"]))
    input_ref = report.nodes[-1].node.input_ref
    assert input_ref["ask"] == "hello"  # legacy key kept
    assert "context" in input_ref  # legacy key kept
    assert input_ref["messages"] == [{"role": "user", "content": "hello"}]  # additive


def test_conversation_history_arrives_as_messages(client, runtime) -> None:
    cid = "11111111-1111-4111-8111-111111111111"
    client.post("/v1/execute", json={"ask": "first", "conversation_id": cid})
    r = client.post("/v1/execute", json={"ask": "second", "conversation_id": cid})
    report = runtime.executions.get(runtime.tenant_id, UUID(r.json()["execution_id"]))
    msgs = report.nodes[-1].node.input_ref["messages"]
    roles = [m["role"] for m in msgs]
    assert roles[-1] == "user" and msgs[-1]["content"] == "second"
    assert "assistant" in roles and "first" in json.dumps(msgs)
