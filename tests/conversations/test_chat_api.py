"""CE-09: projects / conversations / turns / sync chat / streaming into a conversation.

Hermetic: the local-echo adapter answers every turn; the summarizer therefore uses
its extractive fallback (labelled ``summary_source=extractive``).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

from chat_engine.composition.runtime import Runtime
from chat_engine.conversations import (
    ChatRequest,
    Conversation,
    InMemoryConversationStore,
    TurnKind,
    TurnStatus,
)
from tests.streaming.test_five_cases import add_streaming_provider, events_of


def _create_conv(client, **extra):
    r = client.post("/v1/conversations", json={"title": "t", **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_project_crud_and_defaults(client) -> None:
    r = client.post(
        "/v1/projects",
        json={
            "name": "Docs bot",
            "system_instructions": "Always answer in Arabic.",
            "default_constraints": ["no markdown"],
            "default_model": "local-echo-1",
        },
    )
    assert r.status_code == 201, r.text
    project = r.json()
    assert project["name"] == "Docs bot" and project["tenant_id"]

    r = client.patch(f"/v1/projects/{project['id']}", json={"name": "Docs bot v2"})
    assert r.status_code == 200 and r.json()["name"] == "Docs bot v2"
    assert client.get("/v1/projects").json()["total"] == 1

    conv = _create_conv(client, project_id=project["id"])
    assert conv["project_id"] == project["id"]
    r = client.post("/v1/chat", json={"conversation_id": conv["id"], "message": "مرحبا"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider_key"] == "local-echo" and body["model_key"] == "local-echo-1"
    assert body["turn"]["status"] == "complete" and body["handoff"]["packet_id"]
    ex = client.get(f"/v1/executions/{body['execution_id']}").json()
    assert ex["status"] == "succeeded"

    assert client.delete(f"/v1/projects/{project['id']}").status_code == 204
    assert client.get(f"/v1/projects/{project['id']}").status_code == 404
    assert client.get(f"/v1/projects/{uuid4()}").json()["error"]["code"] == "not_found"


def test_project_system_instructions_reach_the_packet(runtime: Runtime) -> None:
    svc = runtime.extensions["chat"]
    from chat_engine.conversations import ConversationCreate, ProjectCreate

    p = svc.create_project(
        ProjectCreate(name="p", system_instructions="SPEAK LIKE A PIRATE", default_constraints=["c1"])
    )
    c = svc.create_conversation(ConversationCreate(project_id=p.id))
    prep = asyncio.run(svc.prepare(ChatRequest(conversation_id=c.id, message="hi")))
    msgs = prep.payload["messages"]
    assert any(m["role"] == "system" and "SPEAK LIKE A PIRATE" in m["content"] for m in msgs)
    assert "c1" in prep.packet.constraints and msgs[-1] == {"role": "user", "content": "hi"}


def test_chat_creates_conversation_and_appends_turns(client) -> None:
    r = client.post("/v1/chat", json={"message": "first question here"})
    assert r.status_code == 200, r.text
    cid = r.json()["conversation_id"]
    conv = client.get(f"/v1/conversations/{cid}").json()
    assert conv["title"] == "first question here" and conv["turn_count"] == 2

    r2 = client.post("/v1/chat", json={"conversation_id": cid, "message": "second"})
    assert r2.status_code == 200
    page = client.get(f"/v1/conversations/{cid}/turns").json()
    assert page["total"] == 4
    assert [t["role"] for t in page["turns"]] == ["user", "assistant", "user", "assistant"]
    assert [t["seq"] for t in page["turns"]] == [1, 2, 3, 4]
    assert r2.json()["handoff"]["estimated_tokens"] > r.json()["handoff"]["estimated_tokens"]
    tail = client.get(f"/v1/conversations/{cid}/turns", params={"after_seq": 2}).json()
    assert [t["seq"] for t in tail["turns"]] == [3, 4] and tail["total"] == 4

    assert client.get("/v1/conversations").json()["total"] == 1
    assert client.patch(f"/v1/conversations/{cid}", json={"archived": True}).status_code == 200
    assert client.get("/v1/conversations").json()["total"] == 0
    assert client.get("/v1/conversations", params={"include_archived": True}).json()["total"] == 1
    assert client.delete(f"/v1/conversations/{cid}").status_code == 204
    assert client.get(f"/v1/conversations/{cid}/turns").status_code == 404


def test_unknown_conversation_and_bad_model(client) -> None:
    r = client.post("/v1/chat", json={"conversation_id": str(uuid4()), "message": "x"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = client.post("/v1/chat", json={"message": "x", "model": "no-such-model"})
    assert r.status_code in (404, 422, 503), r.text
    assert r.json()["error"]["code"] in ("not_found", "alias_unconfirmed", "conflict")
    assert client.post("/v1/chat", json={"message": ""}).status_code == 422


def test_40_turns_trigger_summary_turn_and_cut_point(client) -> None:
    project = client.post(
        "/v1/projects", json={"name": "small ctx", "context_window_hint": 1600}
    ).json()
    conv = _create_conv(client, project_id=project["id"], objective="track the numbers")
    cid = conv["id"]
    for i in range(40):
        msg = f"turn {i}: " + ("lorem ipsum dolor sit amet " * 8)
        r = client.post("/v1/chat", json={"conversation_id": cid, "message": msg})
        assert r.status_code == 200, r.text
    turns = client.get(f"/v1/conversations/{cid}/turns").json()["turns"]
    summaries = [t for t in turns if t["kind"] == "summary"]
    assert summaries, "expected at least one summary turn"
    s = summaries[-1]
    assert s["role"] == "system" and s["metadata"]["summary_source"] == "extractive"
    assert s["metadata"]["covers_turns"]
    assert client.get(f"/v1/conversations/{cid}").json()["summary_turn_id"] == s["id"]
    r = client.post("/v1/chat", json={"conversation_id": cid, "message": "final"})
    assert r.status_code == 200
    assert r.json()["handoff"]["estimated_tokens"] <= 1600


def test_summary_below_threshold_is_noop(runtime: Runtime) -> None:
    svc = runtime.extensions["chat"]
    conv = asyncio.run(svc.chat(ChatRequest(message="alpha beta gamma")))
    c = svc.get_conversation(conv.conversation_id)
    c2 = asyncio.run(svc.maybe_summarize(c, dropped=0))
    assert c2.summary_turn_id is None
    assert all(t.kind is not TurnKind.SUMMARY for t in svc.store.turns(c.id))


def test_stream_into_conversation_persists_complete_turn(client, runtime: Runtime) -> None:
    add_streaming_provider(runtime, "sp9")
    conv = _create_conv(client, model="s-model")
    r = client.post(
        "/v1/chat/stream", json={"conversation_id": conv["id"], "message": "stream me please"}
    )
    assert r.status_code == 200, r.text
    evs = events_of(r)
    assert evs[-1]["type"] == "completed"
    turns = client.get(f"/v1/conversations/{conv['id']}/turns").json()["turns"]
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[1]["status"] == "complete" and turns[1]["stream_id"] == evs[-1]["stream_id"]
    assert "".join(p["text"] for p in turns[1]["parts"]) == "stream me please"
    assert turns[1]["provider_key"] == "sp9" and turns[1]["handoff_packet_id"]


def test_stream_failure_after_deltas_persists_partial_turn(client, runtime: Runtime) -> None:
    add_streaming_provider(runtime, "sp9")
    adapter = add_streaming_provider(runtime, "flaky9", model="flaky-model")
    adapter.fail_after_words = 2
    conv = _create_conv(client, model="flaky-model")
    r = client.post(
        "/v1/chat/stream",
        json={"conversation_id": conv["id"], "message": "one two three four five"},
    )
    evs = events_of(r)
    assert evs[-1]["type"] == "failed" and evs[-1]["data"]["resumable"] is True
    turns = client.get(f"/v1/conversations/{conv['id']}/turns").json()["turns"]
    assert turns[-1]["status"] == "partial"
    assert "".join(p["text"] for p in turns[-1]["parts"]).strip() == "one two"


def test_stream_requires_message_or_messages(client) -> None:
    assert client.post("/v1/chat/stream", json={}).status_code == 422
    assert client.post("/v1/chat/stream", json={"conversation_id": str(uuid4())}).status_code == 422
    r = client.post("/v1/chat/stream", json={"conversation_id": str(uuid4()), "message": "x"})
    assert r.status_code == 404


def test_store_isolation_by_tenant() -> None:
    store = InMemoryConversationStore()
    now = datetime.now(UTC)
    a, b = uuid4(), uuid4()
    c = Conversation(
        id=uuid4(), tenant_id=a, user_id=uuid4(), title="x", created_at=now, updated_at=now
    )
    store.put_conversation(c)
    assert store.list_conversations(a) and not store.list_conversations(b)
    try:
        store.get_conversation(b, c.id)
    except LookupError:
        pass
    else:  # pragma: no cover
        raise AssertionError("cross-tenant read must fail")
    assert store.next_seq(c.id) == 1
    assert json.dumps(c.model_dump(mode="json"))


def test_turn_status_values() -> None:
    assert {s.value for s in TurnStatus} == {"complete", "partial", "failed"}
