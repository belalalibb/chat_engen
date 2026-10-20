"""CE-24/M: POST /v1/conversations/{id}/turns/{turn_id}:feedback → turn.metadata.feedback."""

from __future__ import annotations

from uuid import uuid4

from tests.orchestration.test_strategies import add_scripted


def test_turn_feedback_roundtrip(client, runtime) -> None:
    add_scripted(runtime, "fbp", ["fb-m"], {"fb-m": "an answer"})
    r = client.post("/v1/chat", json={"message": "q", "model": "fb-m"}).json()
    cid, tid, user_tid = r["conversation_id"], r["turn"]["id"], r["user_turn_id"]
    fb = client.post(
        f"/v1/conversations/{cid}/turns/{tid}:feedback",
        json={"score": 1, "comment": "great", "tags": ["accurate"]},
    )
    assert fb.status_code == 200, fb.text
    meta = fb.json()["metadata"]
    assert meta["feedback"]["score"] == 1 and meta["feedback"]["tags"] == ["accurate"]
    # second call replaces, history kept
    fb2 = client.post(f"/v1/conversations/{cid}/turns/{tid}:feedback", json={"score": -1})
    meta2 = fb2.json()["metadata"]
    assert meta2["feedback"]["score"] == -1 and meta2["feedback_history"][0]["score"] == 1
    turns = client.get(f"/v1/conversations/{cid}/turns").json()["turns"]
    assert next(t for t in turns if t["id"] == tid)["metadata"]["feedback"]["score"] == -1
    # validation: user turns rejected, bad score, unknown turn / conversation
    bad = client.post(f"/v1/conversations/{cid}/turns/{user_tid}:feedback", json={"score": 1})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error"
    assert client.post(f"/v1/conversations/{cid}/turns/{tid}:feedback", json={"score": 5}).status_code == 422
    assert client.post(f"/v1/conversations/{cid}/turns/{uuid4()}:feedback", json={"score": 0}).status_code == 404
    assert client.post(f"/v1/conversations/{uuid4()}/turns/{tid}:feedback", json={"score": 0}).status_code == 404
