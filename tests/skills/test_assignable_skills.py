"""CE-19 Assignable Skills: select → assign (profile/project/conversation/explicit) →
enable/disable → version recorded on each turn. Upstream selectability rule reused."""

from __future__ import annotations

from uuid import uuid4

import pytest


def _reg(client, sid: str, version="1.0.0", status="active", instructions=None, **kw):
    r = client.post(
        "/v1/admin/skills",
        json={
            "id": sid,
            "name": sid.replace("_", " ").title(),
            "version": version,
            "status": status,
            "instructions": instructions or f"Follow the {sid} playbook.",
            **kw,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_register_list_detail_upstream_shape(client) -> None:
    assert client.get("/v1/skills").json() == {"skills": []}
    _reg(client, "code_review", requires_tools=["git_diff"])
    _reg(client, "draft_only", status="approved")  # not ACTIVE ⇒ not selectable
    lst = client.get("/v1/skills").json()
    assert lst == {
        "skills": [
            {"id": "code_review", "version": "1.0.0", "status": "active", "requires_tools": ["git_diff"]}
        ]
    }
    d = client.get("/v1/skills/code_review").json()
    assert d["selectable"] is True and d["instructions"].startswith("Follow")
    assert d["registry_id"] and d["versions"] == ["1.0.0"]
    assert client.get("/v1/skills/draft_only").json()["selectable"] is False
    assert client.get("/v1/skills/nope").status_code == 404
    # duplicate version rejected
    r = client.post("/v1/admin/skills", json={"id": "code_review", "name": "x", "version": "1.0.0"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "skill_not_selectable"


def test_turn_records_skill_versions_and_injects_instructions(client) -> None:
    _reg(client, "arabic_tone", instructions="Always reply in formal Arabic.")
    _reg(client, "code_review")
    p = client.post("/v1/projects", json={"name": "p", "skills": [{"skill_id": "arabic_tone"}]}).json()
    r = client.post(
        "/v1/chat", json={"project_id": p["id"], "message": "hi", "skills": None}
    )
    assert r.status_code == 200, r.text
    used = r.json()["turn"]["skills_used"]
    assert [(u["skill_id"], u["version"], u["source"]) for u in used] == [
        ("arabic_tone", "1.0.0", "project")
    ]
    assert used[0]["registry_id"] and used[0]["instructions"].startswith("Always reply")
    # instruction body reached the compiled system message (echo adapter mirrors payload)
    ex = client.get(f"/v1/executions/{r.json()['execution_id']}").json()
    assert ex["status"] == "succeeded"
    turns = client.get(f"/v1/conversations/{r.json()['conversation_id']}/turns").json()["turns"]
    assert turns[0]["skills_used"][0]["skill_id"] == "arabic_tone"  # user turn too


def test_project_system_message_contains_skill_instructions(runtime) -> None:
    import asyncio

    from chat_engine.conversations import ChatRequest, ProjectCreate
    from chat_engine.skills import LocalSkillCreate

    runtime.extensions["skills"].register_local(
        LocalSkillCreate(id="pirate", name="Pirate", instructions="SPEAK LIKE A PIRATE")
    )
    svc = runtime.extensions["chat"]
    p = svc.create_project(ProjectCreate(name="p", skills=[{"skill_id": "pirate"}]))
    prep = asyncio.run(svc.prepare(ChatRequest(project_id=p.id, message="hi")))
    system = prep.payload["messages"][0]
    assert system["role"] == "system" and "SPEAK LIKE A PIRATE" in system["content"]
    assert "pirate@1.0.0" in system["content"]


def test_scope_precedence_and_disable(client) -> None:
    _reg(client, "s_profile")
    _reg(client, "s_project")
    _reg(client, "s_conv")
    prof = client.post(
        "/v1/agent-profiles",
        json={"name": "reviewer", "skills": [{"skill_id": "s_profile"}, {"skill_id": "s_project"}]},
    )
    assert prof.status_code == 201, prof.text
    p = client.post(
        "/v1/projects",
        json={
            "name": "p",
            "agent_profile_id": prof.json()["id"],
            "skills": [{"skill_id": "s_project", "enabled": False}],  # project disables inherited
        },
    ).json()
    c = client.post("/v1/conversations", json={"project_id": p["id"]}).json()
    eff = client.get(f"/v1/conversations/{c['id']}/skills").json()
    assert [(e["skill_id"], e["source"]) for e in eff["effective"]] == [("s_profile", "profile")]
    # conversation re-enables s_project and adds s_conv
    r = client.post(f"/v1/conversations/{c['id']}/skills/s_project:enable")
    assert r.status_code == 200, r.text
    r = client.post(f"/v1/conversations/{c['id']}/skills:assign", json={"skill_id": "s_conv", "config": {"depth": 2}})
    eff = r.json()["effective"]
    assert [(e["skill_id"], e["source"]) for e in eff] == [
        ("s_conv", "conversation"),
        ("s_profile", "profile"),
        ("s_project", "conversation"),
    ]
    assert next(e for e in eff if e["skill_id"] == "s_conv")["config"] == {"depth": 2}
    # conversation disables the profile skill
    client.post(f"/v1/conversations/{c['id']}/skills/s_profile:disable")
    eff = client.get(f"/v1/conversations/{c['id']}/skills").json()["effective"]
    assert "s_profile" not in [e["skill_id"] for e in eff]
    # a turn uses exactly the effective set, with versions
    r = client.post("/v1/chat", json={"conversation_id": c["id"], "message": "go"})
    assert r.status_code == 200
    assert sorted((u["skill_id"], u["version"]) for u in r.json()["turn"]["skills_used"]) == [
        ("s_conv", "1.0.0"),
        ("s_project", "1.0.0"),
    ]
    # explicit per-turn selection wins outright
    r = client.post("/v1/chat", json={"conversation_id": c["id"], "message": "go", "skills": ["s_profile"]})
    assert [(u["skill_id"], u["source"]) for u in r.json()["turn"]["skills_used"]] == [("s_profile", "explicit")]
    # remove
    r = client.delete(f"/v1/conversations/{c['id']}/skills/s_conv")
    assert "s_conv" not in [a["skill_id"] for a in r.json()["assigned"]]


def test_version_pin_and_upgrade_recorded(client) -> None:
    _reg(client, "summarize", version="1.0.0")
    p = client.post("/v1/projects", json={"name": "p", "skills": [{"skill_id": "summarize", "version": "1.0.0"}]}).json()
    r1 = client.post("/v1/chat", json={"project_id": p["id"], "message": "a"})
    assert r1.json()["turn"]["skills_used"][0]["version"] == "1.0.0"
    _reg(client, "summarize", version="2.0.0")
    # pinned project still runs 1.0.0; unpinned conversation gets the newest active
    r2 = client.post("/v1/chat", json={"conversation_id": r1.json()["conversation_id"], "message": "b"})
    assert r2.json()["turn"]["skills_used"][0]["version"] == "1.0.0"
    assert r2.json()["turn"]["skills_used"][0]["requested_version"] == "1.0.0"
    r3 = client.post("/v1/chat", json={"message": "c", "skills": ["summarize"]})
    assert r3.json()["turn"]["skills_used"][0]["version"] == "2.0.0"
    assert client.get("/v1/skills/summarize").json()["versions"] == ["1.0.0", "2.0.0"]
    # pin to a non-existent version → 422
    r = client.post("/v1/projects/%s/skills:assign" % p["id"], json={"skill_id": "summarize", "version": "9.9.9"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "skill_not_selectable"


def test_disabled_skill_rejected_and_unknown_404(client) -> None:
    _reg(client, "risky")
    r = client.post("/v1/chat", json={"message": "x", "skills": ["risky"]})
    assert r.status_code == 200
    assert client.post("/v1/admin/skills/risky:disable").json()["status"] == "disabled"
    assert client.get("/v1/skills").json()["skills"] == []
    r = client.post("/v1/chat", json={"message": "x", "skills": ["risky"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "skill_not_selectable"
    assert client.post("/v1/admin/skills/risky:enable").json()["status"] == "active"
    assert client.post("/v1/chat", json={"message": "x", "skills": ["risky"]}).status_code == 200
    r = client.post("/v1/chat", json={"message": "x", "skills": ["ghost"]})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = client.post("/v1/agent-profiles", json={"name": "bad", "skills": [{"skill_id": "ghost"}]})
    assert r.status_code == 404


def test_agent_profile_crud(client) -> None:
    _reg(client, "a")
    r = client.post("/v1/agent-profiles", json={"name": "P1", "skills": [{"skill_id": "a"}], "default_model": "local-echo-1"})
    pid = r.json()["id"]
    assert client.get("/v1/agent-profiles").json()["total"] == 1
    r = client.patch(f"/v1/agent-profiles/{pid}", json={"name": "P2"})
    assert r.json()["name"] == "P2" and r.json()["skills"][0]["skill_id"] == "a"
    assert client.delete(f"/v1/agent-profiles/{pid}").status_code == 204
    assert client.get(f"/v1/agent-profiles/{pid}").status_code == 404
    assert client.get(f"/v1/agent-profiles/{uuid4()}").status_code == 404


def test_project_replace_assignments(client) -> None:
    _reg(client, "x1")
    _reg(client, "x2")
    p = client.post("/v1/projects", json={"name": "p"}).json()
    r = client.put(f"/v1/projects/{p['id']}/skills", json=[{"skill_id": "x1"}, {"skill_id": "x2", "enabled": False}])
    assert r.status_code == 200, r.text
    assert [e["skill_id"] for e in r.json()["effective"]] == ["x1"]
    assert len(r.json()["assigned"]) == 2
    r = client.put(f"/v1/projects/{p['id']}/skills", json=[{"skill_id": "nope"}])
    assert r.status_code == 404


@pytest.mark.parametrize("path", ["/v1/skills", "/v1/agent-profiles"])
def test_openapi_marks_skill_routes(client, path) -> None:
    schema = client.get("/openapi.json").json()
    assert path in schema["paths"]
