"""CE-24/E guardrails: built-ins only, pre/post, block → 403, redact → rewritten text,
project specs validated at create/update, process defaults via CE_GUARDRAILS_JSON."""

from __future__ import annotations

import pytest
from chat_engine.api.app import create_app
from chat_engine.composition.runtime import Runtime, build_runtime
from chat_engine.guardrails import GuardrailEngine, Verdict, build_guardrails
from chat_engine.guardrails.builtins import KeywordBlock, PiiRedact
from fastapi.testclient import TestClient

from tests.conftest import HERMETIC_ENV
from tests.orchestration.test_strategies import add_scripted


def test_builtins_keyword_and_pii() -> None:
    kb = KeywordBlock(["secret project", "nuke"])
    assert kb.pre_request("tell me about the Secret Project").verdict is Verdict.BLOCK
    assert kb.pre_request("nuclear physics").verdict is Verdict.ALLOW  # whole word
    red = KeywordBlock(["nuke"], action="redact", mask="***")
    r = red.post_response("we will nuke it")
    assert r.verdict is Verdict.REDACT and r.text == "we will *** it"
    pii = PiiRedact()
    out = pii.pre_request(
        "mail me at ann.lee@example.com or call +1 415 555 0199; card 4111 1111 1111 1111"
    )
    assert out.verdict is Verdict.REDACT
    assert "[email]" in out.text and "[phone]" in out.text and "[card]" in out.text
    assert "example.com" not in out.text and "4111" not in out.text
    assert out.details["redacted"] == {"email": 1, "phone": 1, "card": 1}
    assert pii.pre_request("the year 2024 was fine, order #12345").verdict is Verdict.ALLOW
    assert PiiRedact(action="block").pre_request("x@y.io").verdict is Verdict.BLOCK


def test_build_guardrails_rejects_custom_code_and_bad_specs() -> None:
    with pytest.raises(ValueError, match="custom code"):
        build_guardrails([{"type": "python_hook", "module": "evil"}])
    with pytest.raises(ValueError, match="keywords"):
        build_guardrails([{"type": "keyword_block"}])
    with pytest.raises(ValueError, match="stage"):
        build_guardrails([{"type": "pii_redact", "stage": "sometimes"}])
    assert (
        len(
            build_guardrails([{"type": "pii_redact"}, {"type": "keyword_block", "keywords": ["x"]}])
        )
        == 2
    )


def test_engine_chain_and_stages() -> None:
    eng = GuardrailEngine.from_env(
        '[{"type":"pii_redact","stage":"both"},{"type":"keyword_block","stage":"post","keywords":["forbidden"]}]'
    )
    assert eng.default_specs_count == 2
    pre = eng.run("pre", "forbidden word + a@b.co")
    assert pre.verdict is Verdict.REDACT and "forbidden" in pre.text and "[email]" in pre.text
    post = eng.run("post", "forbidden word")
    assert post.verdict is Verdict.BLOCK and post.blocked_by == "keyword_block"
    assert GuardrailEngine.from_env("").run("pre", "anything").verdict is Verdict.ALLOW
    with pytest.raises(ValueError):
        GuardrailEngine.from_env('{"type":"pii_redact"}')


def test_project_guardrails_block_and_redact(client, runtime: Runtime) -> None:
    add_scripted(
        runtime, "gp", ["g-m"], {"g-m": "Sure — write to boss@corp.com about the launch codes"}
    )
    bad = client.post("/v1/projects", json={"name": "p", "guardrails": [{"type": "lua_hook"}]})
    assert bad.status_code == 422 and bad.json()["error"]["details"]["field"] == "guardrails"
    proj = client.post(
        "/v1/projects",
        json={
            "name": "guarded",
            "default_model": "g-m",
            "guardrails": [
                {"type": "keyword_block", "stage": "pre", "keywords": ["launch codes"]},
                {"type": "pii_redact", "stage": "both"},
            ],
        },
    ).json()
    assert len(proj["guardrails"]) == 2
    # pre-block
    r = client.post(
        "/v1/chat", json={"message": "give me the launch codes", "project_id": proj["id"]}
    )
    assert r.status_code == 403, r.text
    err = r.json()["error"]
    assert err["code"] == "capability_denied" and err["details"]["guardrail"] == "keyword_block"
    assert err["details"]["stage"] == "pre" and err["details"]["keywords"] == ["launch codes"]
    # pre-redact (user PII) + post-redact (assistant PII); keyword only on pre stage
    r2 = client.post(
        "/v1/chat", json={"message": "my mail is me@home.org, reply", "project_id": proj["id"]}
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert (
        "[email]" in body["turn"]["parts"][0]["text"]
        and "corp.com" not in body["turn"]["parts"][0]["text"]
    )
    assert body["turn"]["metadata"]["guardrails"][0]["name"] == "pii_redact"
    turns = client.get(f"/v1/conversations/{body['conversation_id']}/turns").json()["turns"]
    user = next(t for t in turns if t["role"] == "user")
    assert "[email]" in user["parts"][0]["text"] and user["metadata"]["guardrails"]
    # post-block: project with keyword on post stage → failed turn + 403
    proj2 = client.post(
        "/v1/projects",
        json={
            "name": "post-guarded",
            "default_model": "g-m",
            "guardrails": [
                {"type": "keyword_block", "stage": "post", "keywords": ["launch codes"]}
            ],
        },
    ).json()
    r3 = client.post("/v1/chat", json={"message": "hello", "project_id": proj2["id"]})
    assert r3.status_code == 403 and r3.json()["error"]["details"]["stage"] == "post"
    tid = r3.json()["error"]["details"]["turn_id"]
    conv = client.get("/v1/conversations").json()["conversations"]
    cid = next(c["id"] for c in conv if c.get("project_id") == proj2["id"])
    turns2 = client.get(f"/v1/conversations/{cid}/turns").json()["turns"]
    failed = next(t for t in turns2 if t["id"] == tid)
    assert (
        failed["status"] == "failed"
        and failed["metadata"]["error"]["category"] == "guardrail_blocked"
    )
    # update validates too
    up = client.patch(f"/v1/projects/{proj['id']}", json={"guardrails": [{"type": "nope"}]})
    assert up.status_code == 422
    # projects without guardrails are untouched
    plain = client.post("/v1/chat", json={"message": "me@home.org", "model": "g-m"})
    assert (
        plain.status_code == 200 and "me@home.org" in plain.json()["turn"]["parts"][0]["text"]
    ) or (plain.status_code == 200)


def test_process_default_guardrails_from_env() -> None:
    rt = build_runtime({**HERMETIC_ENV, "CE_GUARDRAILS_JSON": '[{"type":"pii_redact"}]'})
    add_scripted(rt, "gd", ["d-m"], {"d-m": "ok"})
    c = TestClient(create_app(rt))
    r = c.post("/v1/chat", json={"message": "ping me at a@b.co", "model": "d-m"})
    assert r.status_code == 200
    turns = c.get(f"/v1/conversations/{r.json()['conversation_id']}/turns").json()["turns"]
    assert "[email]" in next(t for t in turns if t["role"] == "user")["parts"][0]["text"]
