#!/usr/bin/env python3
"""chat_engine — Python reference for EVERY request and its specification (v0.1.3).

This single file is the executable twin of ``docs/INTEGRATION_GUIDE.md``: one function per
endpoint, each documenting the exact request body / query / headers and the response shape,
built on the standard library only (``urllib``) so it runs anywhere without installs.

    python reference_client/chat_engine_requests.py                 # run the guided tour
    python reference_client/chat_engine_requests.py --list          # list every request
    python reference_client/chat_engine_requests.py --only chat     # run one section
    python reference_client/chat_engine_requests.py --base-url http://localhost:8080

Endpoint resolution (first wins): ``--base-url`` > ``CHAT_ENGINE_URL`` env >
``reference_client/config/endpoint.json`` (the external, editable location).

Every function returns parsed JSON (or yields SSE events) and raises ``ChatEngineError`` on
``{"error": {...}}``. Nothing here is mocked: the tour talks to the configured server.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error, parse, request

JSON = dict[str, Any]
CONFIG_PATH = Path(__file__).resolve().parent / "config" / "endpoint.json"


# =================================================================================================
# 0. Transport
# =================================================================================================


class ChatEngineError(Exception):
    """Unified error envelope: ``{"error": {code, message, retryable, details, trace_id}}``."""

    def __init__(self, status: int, body: JSON | str):
        self.status = status
        err = body.get("error", {}) if isinstance(body, dict) else {}
        self.code: str = str(err.get("code") or "http_error")
        self.message: str = str(err.get("message") or body)
        self.details: JSON = dict(err.get("details") or {})
        self.retryable: bool = bool(err.get("retryable", False))
        super().__init__(f"{status} {self.code}: {self.message} {self.details or ''}")


@dataclass
class Config:
    base_url: str
    timeout_s: float = 180
    stream_fallback: str | None = "buffer"
    api_key: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, cli_base_url: str | None = None) -> Config:
        data: JSON = {}
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        base = cli_base_url or os.environ.get("CHAT_ENGINE_URL") or data.get("base_url")
        if not base:
            raise SystemExit(
                f"no base_url: pass --base-url, set CHAT_ENGINE_URL, or edit {CONFIG_PATH}"
            )
        return cls(
            base_url=str(base).rstrip("/"),
            timeout_s=float(data.get("timeout_s", 180)),
            stream_fallback=data.get("stream_fallback", "buffer"),
            api_key=os.environ.get("CHAT_ENGINE_API_KEY") or data.get("api_key"),
            headers=dict(data.get("headers") or {}),
        )


class Client:
    """Minimal HTTP client. ``call`` = JSON in/out; ``stream`` = SSE ``data:`` frames."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def _headers(self, extra: dict[str, str] | None = None, *, sse: bool = False) -> dict[str, str]:
        h = {
            "content-type": "application/json",
            "accept": "text/event-stream" if sse else "application/json",
        }
        h.update(self.cfg.headers)
        if self.cfg.api_key:
            h["authorization"] = (
                f"Bearer {self.cfg.api_key}"  # Phase 1: ignored by the server; future-proof
            )
        if extra:
            h.update(extra)
        return h

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        query: JSON | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> JSON:
        url = self.cfg.base_url + path
        if query:
            url += "?" + parse.urlencode({k: v for k, v in query.items() if v is not None})
        data = json.dumps(body).encode() if body is not None else None
        req = request.Request(url, data=data, method=method, headers=self._headers(headers))
        try:
            with request.urlopen(req, timeout=timeout or self.cfg.timeout_s) as r:
                raw = r.read()
                return json.loads(raw) if raw else {"_status": r.status}
        except error.HTTPError as e:
            raw = e.read()
            try:
                parsed: JSON | str = json.loads(raw)
            except Exception:
                parsed = raw.decode(errors="replace")
            raise ChatEngineError(e.code, parsed) from None

    def stream(self, path: str, body: JSON, *, timeout: float | None = None) -> Iterator[JSON]:
        """Yield one dict per SSE ``data:`` line. Stops after the stream closes."""
        req = request.Request(
            self.cfg.base_url + path,
            data=json.dumps(body).encode(),
            method="POST",
            headers=self._headers(sse=True),
        )
        try:
            resp = request.urlopen(req, timeout=timeout or self.cfg.timeout_s)
        except error.HTTPError as e:
            raw = e.read()
            try:
                raise ChatEngineError(e.code, json.loads(raw)) from None
            except ValueError:
                raise ChatEngineError(e.code, raw.decode(errors="replace")) from None
        with resp:
            for line in resp:
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if text.startswith("data:"):
                    payload = text[5:].strip()
                    if payload and payload != "[DONE]":
                        yield json.loads(payload)


# =================================================================================================
# 1. Discovery  — "what is available right now?"
# =================================================================================================


def healthz(c: Client) -> JSON:
    """GET /healthz
    → {status:"alive", version, upstream_sha, providers[], gateway:{bound[], skipped{}},
       admission_backend, replicas_safe}
    `gateway.bound` = gateway routes that answered /v1/describe at boot (bound ≠ answering now)."""
    return c.call("GET", "/healthz")


def openai_models(c: Client) -> JSON:
    """GET /v1/openai/models
    → {object:"list", data:[{id, object:"model", created:0, owned_by}]}
    `id` is accepted directly in the `model` field everywhere (canonical_id or model_key)."""
    return c.call("GET", "/v1/openai/models")


def openai_model(c: Client, model_id: str) -> JSON:
    """GET /v1/openai/models/{id} → one row or 404 model_not_found."""
    return c.call("GET", f"/v1/openai/models/{parse.quote(model_id, safe='')}")


def models(c: Client) -> JSON:
    """GET /v1/models (upstream-compatible)
    → {models:[{name, providers[], capabilities[], ...}]} — which provider serves which model."""
    return c.call("GET", "/v1/models")


def catalog_models(c: Client, *, offset: int = 0, limit: int = 50, q: str | None = None) -> JSON:
    """GET /v1/catalog/models?offset&limit&q  → {items[], total, offset, limit} (10k+ rows, paged)."""
    return c.call("GET", "/v1/catalog/models", query={"offset": offset, "limit": limit, "q": q})


def catalog_model(c: Client, canonical_id: str) -> JSON:
    """GET /v1/catalog/models/{canonical_id} → canonical row (vendor, context, pricing, aliases)."""
    return c.call("GET", f"/v1/catalog/models/{parse.quote(canonical_id, safe='')}")


def catalog_aliases(c: Client, *, alias: str | None = None, decision: str | None = None) -> JSON:
    """GET /v1/catalog/aliases?alias&decision  (decision ∈ confirmed|proposed|rejected)."""
    return c.call("GET", "/v1/catalog/aliases", query={"alias": alias, "decision": decision})


def catalog_alias_decide(c: Client, alias: str, canonical_id: str, decision: str) -> JSON:
    """POST /v1/catalog/aliases/{alias}:decide  body {canonical_id, decision: confirmed|rejected}
    Proposed aliases are never used for routing until confirmed (else 422 alias_unconfirmed)."""
    return c.call(
        "POST",
        f"/v1/catalog/aliases/{parse.quote(alias, safe='')}:decide",
        {"canonical_id": canonical_id, "decision": decision},
    )


def admission_snapshot(c: Client) -> JSON:
    """GET /v1/admin/admission
    → {backend, replicas_safe, scopes:[{scope, limits:{rpm,concurrency,source}, rpm_used, in_flight, budget_used, breaker_open}]}
    Scopes: `provider:<key>` (rpm 60 / concurrency 4 conservative default) and `tenant:<id>`."""
    return c.call("GET", "/v1/admin/admission")


# =================================================================================================
# 2. Chat (sync)  — POST /v1/chat — the primary request
# =================================================================================================


def chat(
    c: Client,
    message: str,
    *,
    conversation_id: str | None = None,
    project_id: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    generation: JSON | None = None,
    tools: list[JSON] | None = None,
    required_capabilities: list[str] | None = None,
    skills: list[str] | None = None,
    attachments: list[JSON] | None = None,
    strategy: str | None = None,
    strategy_options: JSON | None = None,
) -> JSON:
    """POST /v1/chat   (ContractModel, extra=forbid — unknown keys ⇒ 422)

    body:
      message                str   required, 1..400_000 chars
      conversation_id        uuid  continue a thread; None ⇒ a new conversation is created
      project_id             uuid  new conversation inherits project instructions/model/skills
      model                  str   canonical_id or model_key; None ⇒ AUTO routing + failover
      provider               str   pin a provider key (e.g. "gw_deepai"); None ⇒ router decides
      generation             obj   {temperature, top_p, max_tokens, stop[], response_format{type:"json_object"}}
      tools                  list  OpenAI-style tool specs
      required_capabilities  list  e.g. ["tools","vision"] — router filters providers
      skills                 list  explicit skill_ids for this turn (overrides project/conversation)
      attachments            list  reserved
      strategy               str   single | sequential_handoff | judge_select | cost_tiered
      strategy_options       obj   see `chat_cost_tiered` / `chat_sequential_handoff` / `chat_judge_select`
      stream_fallback        str   accepted & ignored (body parity with /v1/chat/stream)

    → ChatResponse {conversation_id, user_turn_id, turn:{id, seq, role:"assistant", parts:[{type:"text",text}],
         status: complete|partial|failed, provider_key, model_key, canonical_id, usage, latency_ms,
         execution_id, skills_used[], metadata}, execution_id, provider_key, model_key, usage,
         handoff:{packet_id, estimated_tokens, dropped_messages, summarized}, trace|null}

    errors: 422 validation_error|alias_unconfirmed|conflict · 404 not_found (missing conversation
      OR no routable candidate: details.excluded[]) · 429 admission_denied (details.scope/dimension/
      retry_after_ms) · 502 execution_failed (details.provider_error_category) · 503 provider_unavailable
    """
    body: JSON = {"message": message}
    for k, v in {
        "conversation_id": conversation_id,
        "project_id": project_id,
        "model": model,
        "provider": provider,
        "generation": generation,
        "tools": tools,
        "required_capabilities": required_capabilities,
        "skills": skills,
        "attachments": attachments,
        "strategy": strategy,
        "strategy_options": strategy_options,
    }.items():
        if v is not None:
            body[k] = v
    return c.call("POST", "/v1/chat", body)


def text_of(resp: JSON) -> str:
    """Extract the assistant text from a ChatResponse (or a Turn)."""
    turn = resp.get("turn", resp)
    return "".join(p.get("text", "") for p in turn.get("parts", []) if p.get("type") == "text")


def chat_auto(c: Client, message: str, **kw: Any) -> JSON:
    """Recommended default: no model, no provider — AUTO + max_escalation failover."""
    return chat(c, message, **kw)


def chat_explicit(
    c: Client, message: str, model: str, provider: str | None = None, **kw: Any
) -> JSON:
    """Pin a model (and optionally the provider). Live-verified pairs (2026-10-18):
    ("deepseek-v3.2","gw_deepai"), ("claude-4.5-haiku","gw_overchat"), ("gpt-oss-120b","gw_llamacoder")."""
    return chat(c, message, model=model, provider=provider, **kw)


def chat_json(c: Client, message: str, **kw: Any) -> JSON:
    """Force a JSON object answer: generation.response_format={"type":"json_object"}, temperature 0."""
    return chat(
        c, message, generation={"response_format": {"type": "json_object"}, "temperature": 0}, **kw
    )


def chat_cost_tiered(
    c: Client,
    message: str,
    *,
    tiers: list[str] | None = None,
    output_spec: JSON | None = None,
    **kw: Any,
) -> JSON:
    """strategy=cost_tiered — cheapest first; one upgrade if output_spec fails; failover on refusal.
    strategy_options {tiers:[cheap, better] (derived when empty), output_spec:{min_chars,max_chars,
      must_include[], must_not_include[], json_object, language_hint}, provider?}
    → response.trace {strategy, selected_step, decision_reason, steps:[{step, role:"tier", model,
      provider_key, model_key, status, spec_score, spec_failures[], reason, latency_ms}], upgrades}"""
    opts: JSON = {}
    if tiers:
        opts["tiers"] = tiers
    if output_spec:
        opts["output_spec"] = output_spec
    return chat(c, message, strategy="cost_tiered", strategy_options=opts, **kw)


def chat_sequential_handoff(
    c: Client,
    message: str,
    stages: list[str],
    *,
    stage_instructions: list[str] | None = None,
    output_spec: JSON | None = None,
    **kw: Any,
) -> JSON:
    """strategy=sequential_handoff — draft by stages[0], refined by stages[1..] (≥2 else 422).
    strategy_options {stages[], stage_instructions[]?, output_spec?}"""
    opts: JSON = {"stages": stages}
    if stage_instructions:
        opts["stage_instructions"] = stage_instructions
    if output_spec:
        opts["output_spec"] = output_spec
    return chat(c, message, strategy="sequential_handoff", strategy_options=opts, **kw)


def chat_judge_select(
    c: Client,
    message: str,
    candidates: list[str],
    *,
    judge: str | None = None,
    max_candidates: int = 3,
    output_spec: JSON | None = None,
    **kw: Any,
) -> JSON:
    """strategy=judge_select — candidates in parallel, then a judge model (or heuristic on output_spec).
    strategy_options {candidates[] (≥2), judge?: model|None, max_candidates 2..6, output_spec?}"""
    opts: JSON = {"candidates": candidates, "max_candidates": max_candidates}
    if judge:
        opts["judge"] = judge
    if output_spec:
        opts["output_spec"] = output_spec
    return chat(c, message, strategy="judge_select", strategy_options=opts, **kw)


# =================================================================================================
# 3. Streaming — POST /v1/chat/stream (SSE) + cancel / resume / summary
# =================================================================================================


def chat_stream(
    c: Client,
    *,
    message: str | None = None,
    messages: list[JSON] | None = None,
    conversation_id: str | None = None,
    project_id: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    model_policy: JSON | None = None,
    objective: str | None = None,
    constraints: list[str] | None = None,
    generation: JSON | None = None,
    tools: list[JSON] | None = None,
    stream_fallback: str | None = "buffer",
    deadline_ms: int | None = None,
    required_capabilities: list[str] | None = None,
    skills: list[str] | None = None,
) -> Iterator[JSON]:
    """POST /v1/chat/stream   headers: accept: text/event-stream

    Two modes:
      conversation-backed: {conversation_id? | project_id?, message, ...}  (turns are persisted)
      stateless:           {messages:[{role, content, name?, tool_call_id?}], ...}
    stream_fallback="buffer": REQUIRED for gateway (sync-only) providers — one synthesized burst,
      events tagged synthesized:true. Without it: failed{reason:"unsupported_capability"} (honest).
    deadline_ms ≥ 1000. model_policy is the advanced alternative to `model`.

    yields StreamEvent {seq, event_id, type, stream_id, data}
      started{provider_key, model_key} → message_delta{delta} / tool_call_start|delta|end /
      usage{...} / heartbeat → TERMINAL one of:
      completed{provider_key, model_key, usage, failovers, text_length}
      failed{reason ∈ stream_interrupted|provider_error|no_candidate|first_byte_timeout|idle_timeout|
             deadline_exceeded|unsupported_capability|admission_denied, resumable, delivered_until_seq, detail}
      cancelled{delivered_until_seq, partial_text_length}
    """
    body: JSON = {}
    for k, v in {
        "message": message,
        "messages": messages,
        "conversation_id": conversation_id,
        "project_id": project_id,
        "model": model,
        "provider": provider,
        "model_policy": model_policy,
        "objective": objective,
        "constraints": constraints,
        "generation": generation,
        "tools": tools,
        "stream_fallback": stream_fallback,
        "deadline_ms": deadline_ms,
        "required_capabilities": required_capabilities,
        "skills": skills,
    }.items():
        if v is not None:
            body[k] = v
    return c.stream("/v1/chat/stream", body)


def stream_cancel(c: Client, stream_id: str) -> JSON:
    """POST /v1/chat/stream/{id}:cancel → {stream_id, cancelled: bool}. Frees the provider slot
    immediately (also in buffer mode — v0.1.3)."""
    return c.call("POST", f"/v1/chat/stream/{stream_id}:cancel")


def stream_summary(c: Client, stream_id: str) -> JSON:
    """GET /v1/chat/stream/{id} → {stream_id, status, delivered_seq, text?, resumable, reason, failovers}."""
    return c.call("GET", f"/v1/chat/stream/{stream_id}")


def stream_resume(c: Client, stream_id: str, delivered_until_seq: int) -> Iterator[JSON]:
    """POST /v1/chat/stream/{id}:resume  body {delivered_until_seq}
    Continues a failed{resumable:true} stream; already-delivered text is never re-sent."""
    return c.stream(
        f"/v1/chat/stream/{stream_id}:resume", {"delivered_until_seq": delivered_until_seq}
    )


def collect_stream(events: Iterator[JSON]) -> tuple[str, JSON | None, str | None]:
    """Helper: consume a stream → (full_text, terminal_event, stream_id)."""
    text: list[str] = []
    terminal: JSON | None = None
    sid: str | None = None
    for ev in events:
        sid = sid or ev.get("stream_id")
        if ev["type"] == "message_delta":
            text.append(str(ev["data"].get("delta", "")))
        if ev["type"] in ("completed", "failed", "cancelled"):
            terminal = ev
    return "".join(text), terminal, sid


# =================================================================================================
# 4. Projects & Conversations — server-side memory
# =================================================================================================


def create_project(c: Client, name: str, **fields: Any) -> JSON:
    """POST /v1/projects → 201 Project
    body {name, system_instructions?, default_model?, default_constraints[], summarize_with?,
          context_window_hint?(≥1), skills:[{skill_id, version?, enabled, config{}}], agent_profile_id?}"""
    return c.call("POST", "/v1/projects", {"name": name, **fields})


def list_projects(c: Client) -> JSON:
    """GET /v1/projects → {projects[], total}"""
    return c.call("GET", "/v1/projects")


def get_project(c: Client, project_id: str) -> JSON:
    """GET /v1/projects/{id}"""
    return c.call("GET", f"/v1/projects/{project_id}")


def update_project(c: Client, project_id: str, **fields: Any) -> JSON:
    """PATCH /v1/projects/{id}  body: any subset of the create fields."""
    return c.call("PATCH", f"/v1/projects/{project_id}", fields)


def delete_project(c: Client, project_id: str) -> JSON:
    """DELETE /v1/projects/{id} → 204"""
    return c.call("DELETE", f"/v1/projects/{project_id}")


def create_conversation(c: Client, **fields: Any) -> JSON:
    """POST /v1/conversations → 201 Conversation
    body {title?, project_id?, model?, objective?, constraints[], skills[], metadata{}}"""
    return c.call("POST", "/v1/conversations", fields)


def list_conversations(
    c: Client,
    *,
    project_id: str | None = None,
    include_archived: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> JSON:
    """GET /v1/conversations?project_id&include_archived&limit&offset → {conversations[], total}"""
    return c.call(
        "GET",
        "/v1/conversations",
        query={
            "project_id": project_id,
            "include_archived": str(include_archived).lower(),
            "limit": limit,
            "offset": offset,
        },
    )


def get_conversation(c: Client, conversation_id: str) -> JSON:
    """GET /v1/conversations/{id} → {id, title, project_id, model, turn_count, summary_turn_id, archived, ...}"""
    return c.call("GET", f"/v1/conversations/{conversation_id}")


def update_conversation(c: Client, conversation_id: str, **fields: Any) -> JSON:
    """PATCH /v1/conversations/{id}  body {title?, model?, objective?, constraints?, archived?: bool}"""
    return c.call("PATCH", f"/v1/conversations/{conversation_id}", fields)


def delete_conversation(c: Client, conversation_id: str) -> JSON:
    """DELETE /v1/conversations/{id} → 204"""
    return c.call("DELETE", f"/v1/conversations/{conversation_id}")


def turns(
    c: Client, conversation_id: str, *, after_seq: int | None = None, limit: int = 200
) -> JSON:
    """GET /v1/conversations/{id}/turns?after_seq&limit → {turns[], total, conversation_id}
    Turn.seq starts at 1; kind ∈ message|summary|system|tool; status ∈ complete|partial|failed."""
    return c.call(
        "GET",
        f"/v1/conversations/{conversation_id}/turns",
        query={"after_seq": after_seq, "limit": limit},
    )


# =================================================================================================
# 5. Skills & Agent Profiles
# =================================================================================================


def register_skill(
    c: Client, skill_id: str, name: str, instructions: str, *, version: str = "1.0.0", **fields: Any
) -> JSON:
    """POST /v1/admin/skills → 201
    body {id, name, version="1.0.0", type="instruction", instructions, capabilities[], requires_tools[],
          permissions_requested[], compatible_roles[], status="active"}"""
    return c.call(
        "POST",
        "/v1/admin/skills",
        {"id": skill_id, "name": name, "version": version, "instructions": instructions, **fields},
    )


def list_skills(c: Client) -> JSON:
    """GET /v1/skills (upstream-compatible list)"""
    return c.call("GET", "/v1/skills")


def get_skill(c: Client, skill_id: str) -> JSON:
    """GET /v1/skills/{id}"""
    return c.call("GET", f"/v1/skills/{skill_id}")


def disable_skill_globally(c: Client, skill_id: str) -> JSON:
    """POST /v1/admin/skills/{id}:disable — assigning it afterwards ⇒ 422 skill_not_selectable."""
    return c.call("POST", f"/v1/admin/skills/{skill_id}:disable")


def _scope(kind: str, obj_id: str) -> str:
    assert kind in ("projects", "conversations")
    return f"/v1/{kind}/{obj_id}/skills"


def assign_skill(
    c: Client,
    kind: str,
    obj_id: str,
    skill_id: str,
    *,
    version: str | None = None,
    enabled: bool = True,
    config: JSON | None = None,
) -> JSON:
    """POST /v1/{projects|conversations}/{id}/skills:assign  body {skill_id, version?, enabled, config{}} (upsert one)"""
    body: JSON = {"skill_id": skill_id, "enabled": enabled, "config": config or {}}
    if version:
        body["version"] = version
    return c.call("POST", _scope(kind, obj_id) + ":assign", body)


def replace_skills(c: Client, kind: str, obj_id: str, assignments: list[JSON]) -> JSON:
    """PUT /v1/{projects|conversations}/{id}/skills  body [{skill_id, version?, enabled, config}] (replace list)"""
    return c.call("PUT", _scope(kind, obj_id), assignments)


def list_assigned_skills(c: Client, kind: str, obj_id: str) -> JSON:
    """GET /v1/{projects|conversations}/{id}/skills"""
    return c.call("GET", _scope(kind, obj_id))


def enable_skill(c: Client, kind: str, obj_id: str, skill_id: str) -> JSON:
    """POST …/skills/{skill_id}:enable"""
    return c.call("POST", f"{_scope(kind, obj_id)}/{skill_id}:enable")


def disable_skill(c: Client, kind: str, obj_id: str, skill_id: str) -> JSON:
    """POST …/skills/{skill_id}:disable (conversation-level disable overrides project)"""
    return c.call("POST", f"{_scope(kind, obj_id)}/{skill_id}:disable")


def remove_skill(c: Client, kind: str, obj_id: str, skill_id: str) -> JSON:
    """DELETE …/skills/{skill_id}"""
    return c.call("DELETE", f"{_scope(kind, obj_id)}/{skill_id}")


def create_agent_profile(c: Client, name: str, **fields: Any) -> JSON:
    """POST /v1/agent-profiles → 201
    body {name, description?, system_instructions?, default_model?, skills:[{skill_id,...}], metadata{}}
    Then: create_project(..., agent_profile_id=profile["id"])."""
    return c.call("POST", "/v1/agent-profiles", {"name": name, **fields})


def list_agent_profiles(c: Client) -> JSON:
    """GET /v1/agent-profiles"""
    return c.call("GET", "/v1/agent-profiles")


def get_agent_profile(c: Client, profile_id: str) -> JSON:
    """GET /v1/agent-profiles/{id}"""
    return c.call("GET", f"/v1/agent-profiles/{profile_id}")


def update_agent_profile(c: Client, profile_id: str, **fields: Any) -> JSON:
    """PATCH /v1/agent-profiles/{id}"""
    return c.call("PATCH", f"/v1/agent-profiles/{profile_id}", fields)


def delete_agent_profile(c: Client, profile_id: str) -> JSON:
    """DELETE /v1/agent-profiles/{id} → 204"""
    return c.call("DELETE", f"/v1/agent-profiles/{profile_id}")


# =================================================================================================
# 6. OpenAI-compatible surface — drop-in for existing OpenAI clients
# =================================================================================================


def chat_completions(
    c: Client,
    model: str,
    messages: list[JSON],
    *,
    stream: bool = False,
    provider: str | None = None,
    **params: Any,
) -> JSON | Iterator[JSON]:
    """POST /v1/chat/completions
    body {model, messages[{role, content}], stream=false, temperature?, top_p?, max_tokens?|max_completion_tokens?,
          stop?, tools?, tool_choice?, response_format?, user?, n=1, stream_options?,
          chat_engine extensions: provider?, required_capabilities[], skills[], conversation_id?}
    sync → chat.completion + extra `chat_engine:{execution_id, provider_key, model_key, handoff_packet_id, latency_ms}`
    stream → chat.completion.chunk frames … then `data: [DONE]`.
      NOTE: gateway providers are sync-only; this surface has no buffer fallback (OpenAI wire has no such
      knob), so stream=true against a gw_* model ends with finish_reason:"error" code unsupported_capability.
      Use stream=false here, or /v1/chat/stream with stream_fallback="buffer".
    errors → {"error": {message, type, code, param}} (OpenAI shape, same codes as §0)."""
    body: JSON = {"model": model, "messages": messages, "stream": stream, **params}
    if provider:
        body["provider"] = provider
    if stream:
        return c.stream("/v1/chat/completions", body)
    return c.call("POST", "/v1/chat/completions", body)


# =================================================================================================
# 7. Upstream-compatible execute surface
# =================================================================================================


def execute(c: Client, ask: str, *, idempotency_key: str | None = None, **fields: Any) -> JSON:
    """POST /v1/execute   header Idempotency-Key? (same key+body ⇒ same result; different body ⇒ 409)
    body {ask, model_policy?: {type:"auto"} | {type:"explicit", model_id}, execution_policy?: {async: bool},
          payload?{}, required_capabilities[]}
    sync → {execution:{id, status: succeeded|failed, ...}, nodes[], ...}
    async → 202 {execution_id}; then get_execution / execution_events.
    Errors use UPSTREAM codes only (entitlement_exceeded 403, execution_failed 502, rate_limited 429 …)."""
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
    return c.call("POST", "/v1/execute", {"ask": ask, **fields}, headers=headers)


def list_executions(c: Client) -> JSON:
    """GET /v1/executions"""
    return c.call("GET", "/v1/executions")


def get_execution(c: Client, execution_id: str) -> JSON:
    """GET /v1/executions/{id}"""
    return c.call("GET", f"/v1/executions/{execution_id}")


def execution_events(c: Client, execution_id: str) -> JSON:
    """GET /v1/executions/{id}/events (lifecycle events; SSE when accept: text/event-stream)"""
    return c.call("GET", f"/v1/executions/{execution_id}/events")


# =================================================================================================
# 8. Usage & observability
# =================================================================================================


def usage(c: Client) -> JSON:
    """GET /v1/usage → aggregates per tenant/provider/model"""
    return c.call("GET", "/v1/usage")


def usage_records(c: Client, **query: Any) -> JSON:
    """GET /v1/usage/records → {records:[{request_id, surface, status, provider_key, model_key, usage, latency_ms, conversation_id, metadata}]}"""
    return c.call("GET", "/v1/usage/records", query=query or None)


def usage_record(c: Client, request_id: str) -> JSON:
    """GET /v1/usage/records/{request_id}"""
    return c.call("GET", f"/v1/usage/records/{request_id}")


# =================================================================================================
# 9. Admin — providers lifecycle, BYOK credentials, capabilities, pricing
# =================================================================================================


def admin_providers(c: Client) -> JSON:
    """GET /v1/admin/providers → {providers:[{provider_key, display_name, status, managed, lifecycle_state, revision, operations[], model_count}]}
    Gateway/env-bound providers are managed=false (no lifecycle; GET /v1/admin/providers/{key} ⇒ 404 by design)."""
    return c.call("GET", "/v1/admin/providers")


def admin_providers_managed(c: Client) -> JSON:
    """GET /v1/admin/providers/managed — only lifecycle-managed (OpenAI-compatible drafts)."""
    return c.call("GET", "/v1/admin/providers/managed")


def admin_provider(c: Client, key: str) -> JSON:
    """GET /v1/admin/providers/{key} (managed only)"""
    return c.call("GET", f"/v1/admin/providers/{key}")


def admin_provider_draft(
    c: Client,
    key: str,
    display_name: str,
    base_url: str,
    *,
    static_models: list[str] | None = None,
    credential_ref: str | None = None,
    extra_headers: dict[str, str] | None = None,
    **fields: Any,
) -> JSON:
    """POST /v1/admin/providers/{key} → 201 (draft; PUT = update draft)
    body {display_name, base_url, kind="openai_compatible", credential_ref?, static_models[], extra_headers{}, capabilities?, limits{}, notes?}
    Lifecycle: draft → :validate → :verify → :enable ⇄ :disable → :archive ; :rollback {revision}; GET …/revisions"""
    body: JSON = {
        "display_name": display_name,
        "base_url": base_url,
        "static_models": static_models or [],
        "extra_headers": extra_headers or {},
        **fields,
    }
    if credential_ref:
        body["credential_ref"] = credential_ref
    return c.call("POST", f"/v1/admin/providers/{key}", body)


def admin_provider_transition(
    c: Client, key: str, action: str, *, reason: str | None = None, revision: int | None = None
) -> JSON:
    """POST /v1/admin/providers/{key}:{validate|verify|enable|disable|archive|rollback}  body {reason?, changed_by="admin", revision? (rollback)}"""
    body: JSON = {}
    if reason:
        body["reason"] = reason
    if revision is not None:
        body["revision"] = revision
    return c.call("POST", f"/v1/admin/providers/{key}:{action}", body or None)


def admin_provider_revisions(c: Client, key: str) -> JSON:
    """GET /v1/admin/providers/{key}/revisions → {revisions:[{revision, state, reason, changed_by, at}]} (append-only)"""
    return c.call("GET", f"/v1/admin/providers/{key}/revisions")


def create_credential(
    c: Client,
    label: str,
    secret: str,
    *,
    provider_hint: str | None = None,
    owner_type: str = "user",
    policy: str = "prefer_user",
) -> JSON:
    """POST /v1/credentials → 201 {id, credential_ref, fingerprint, last4, label, provider_hint}
    body {label, secret (8..4096, NEVER echoed back), owner_type=user, provider_hint?, policy=prefer_user}
    Use the returned credential_ref in admin_provider_draft(credential_ref=...)."""
    body: JSON = {"label": label, "secret": secret, "owner_type": owner_type, "policy": policy}
    if provider_hint:
        body["provider_hint"] = provider_hint
    return c.call("POST", "/v1/credentials", body)


def list_credentials(c: Client) -> JSON:
    """GET /v1/credentials — fingerprints + last4 only."""
    return c.call("GET", "/v1/credentials")


def delete_credential(c: Client, credential_id: str) -> JSON:
    """DELETE /v1/credentials/{id}"""
    return c.call("DELETE", f"/v1/credentials/{credential_id}")


def capabilities_ledger(c: Client) -> JSON:
    """GET /v1/admin/capabilities/ledger — per provider×capability: declared ∧ verified ∧ enabled ∧ available_now"""
    return c.call("GET", "/v1/admin/capabilities/ledger")


def capabilities_catalog(c: Client) -> JSON:
    """GET /v1/admin/capabilities/catalog"""
    return c.call("GET", "/v1/admin/capabilities/catalog")


def capabilities_probes(c: Client) -> JSON:
    """GET /v1/admin/capabilities/probes"""
    return c.call("GET", "/v1/admin/capabilities/probes")


def capability_probe(c: Client, provider_key: str, capability: str) -> JSON:
    """POST /v1/admin/capabilities/{provider}/{capability}:probe → {probe_id, provider_key, capability, passed, detail}"""
    return c.call("POST", f"/v1/admin/capabilities/{provider_key}/{capability}:probe")


def capability_enable(c: Client, provider_key: str, capability: str) -> JSON:
    """POST /v1/admin/capabilities/{provider}/{capability}:enable"""
    return c.call("POST", f"/v1/admin/capabilities/{provider_key}/{capability}:enable")


def admin_models(c: Client) -> JSON:
    """GET /v1/admin/models"""
    return c.call("GET", "/v1/admin/models")


def admin_pricing(c: Client) -> JSON:
    """GET /v1/admin/pricing ; PUT /v1/admin/pricing {rows:[{provider_key, model_key, input_per_1k, output_per_1k, currency}]}"""
    return c.call("GET", "/v1/admin/pricing")


def admin_routing_weights(c: Client) -> JSON:
    """GET /v1/admin/routing/weights"""
    return c.call("GET", "/v1/admin/routing/weights")


# =================================================================================================
# 10. Robust-call pattern (the playbook from INTEGRATION_GUIDE §10, as code)
# =================================================================================================


def chat_robust(c: Client, message: str, *, max_attempts: int = 3, **kw: Any) -> JSON:
    """chat() with the recommended recovery policy:
    - 429 on a provider scope while a provider/model was pinned → drop the pin (AUTO) and retry
    - 429 otherwise → sleep details.retry_after_ms then retry
    - 502 execution_failed with a pin → drop the pin and retry once
    Never more than `max_attempts`."""
    body = dict(kw)
    for attempt in range(1, max_attempts + 1):
        try:
            return chat(c, message, **body)
        except ChatEngineError as e:
            pinned = bool(body.get("provider") or body.get("model"))
            if attempt == max_attempts:
                raise
            if e.status == 429:
                if pinned and str(e.details.get("scope", "")).startswith("provider:"):
                    body.pop("provider", None)
                    body.pop("model", None)
                    continue
                time.sleep(min(30, (e.details.get("retry_after_ms") or 1000) / 1000))
                continue
            if e.status == 502 and pinned:
                body.pop("provider", None)
                body.pop("model", None)
                continue
            raise
    raise RuntimeError("unreachable")


# =================================================================================================
# 11. Guided tour — executes the reference requests against the configured endpoint
# =================================================================================================


def _p(title: str, obj: Any, limit: int = 320) -> None:
    s = json.dumps(obj, ensure_ascii=False) if not isinstance(obj, str) else obj
    print(f"\n## {title}\n{s[:limit]}{' …' if len(s) > limit else ''}", flush=True)


def tour(c: Client, only: str | None = None) -> int:
    def want(section: str) -> bool:
        return only is None or only == section

    failures = 0
    conv_id: str | None = None
    proj_id: str | None = None

    if want("discovery"):
        h = healthz(c)
        _p("healthz", {k: h.get(k) for k in ("status", "version", "providers", "gateway")})
        _p("openai_models (first 5)", openai_models(c)["data"][:5])
        _p("catalog_models q=deepseek", catalog_models(c, limit=3, q="deepseek"))
        _p("admission (first 2 scopes)", admission_snapshot(c)["scopes"][:2])

    if want("chat"):
        try:
            r = chat_auto(c, "ما عاصمة مصر؟ كلمة واحدة.")
            conv_id = r["conversation_id"]
            _p(
                "chat AUTO",
                {"answered_by": f"{r['provider_key']}/{r['model_key']}", "text": text_of(r)},
            )
            r = chat(c, "وما عدد سكانها تقريبًا؟ رقم فقط.", conversation_id=conv_id)
            _p("chat follow-up (memory)", text_of(r))
            r = chat_json(c, 'أعطني {"city": ..., "country": ...} لمدينة باريس')
            _p("chat JSON format", text_of(r))
            r = chat_explicit(c, "Reply with exactly: OK", "deepseek-v3.2", "gw_deepai")
            _p("chat explicit gw_deepai/deepseek-v3.2", text_of(r))
        except ChatEngineError as e:
            failures += 1
            _p("chat ERROR", str(e))

    if want("stream"):
        try:
            text, term, sid = collect_stream(
                chat_stream(c, message="اكتب جملة واحدة عن البحر.", conversation_id=conv_id)
            )
            _p("stream", {"stream_id": sid, "terminal": term and term["type"], "text": text})
            if sid:
                _p("stream_summary", stream_summary(c, sid))
        except ChatEngineError as e:
            failures += 1
            _p("stream ERROR", str(e))

    if want("strategies"):
        for name, fn in (
            (
                "cost_tiered",
                lambda: chat_cost_tiered(
                    c,
                    "عرّف API في ≤ 200 حرف بالعربية",
                    output_spec={"max_chars": 200, "language_hint": "ar"},
                ),
            ),
            (
                "judge_select",
                lambda: chat_judge_select(
                    c,
                    "اقترح اسمًا لمنتج قهوة (≤ 40 حرفًا)",
                    ["deepseek-v3.2", "claude-4.5-haiku"],
                    output_spec={"max_chars": 40},
                ),
            ),
            (
                "sequential_handoff",
                lambda: chat_sequential_handoff(
                    c, "جملة واحدة عن الشاي.", ["deepseek-v3.2", "deepseek-v4-flash"]
                ),
            ),
        ):
            try:
                r = fn()
                tr = r.get("trace") or {}
                _p(
                    f"strategy {name}",
                    {
                        "answered_by": f"{r['provider_key']}/{r['model_key']}",
                        "selected_step": tr.get("selected_step"),
                        "steps": len(tr.get("steps", [])),
                        "text": text_of(r)[:80],
                    },
                )
            except ChatEngineError as e:
                failures += 1
                _p(f"strategy {name} ERROR", str(e))

    if want("memory"):
        try:
            p = create_project(
                c,
                "reference-client demo",
                system_instructions="أجب بالعربية بإيجاز شديد.",
                default_model="deepseek-v3.2",
            )
            proj_id = p["id"]
            r = chat(c, "من أنت؟", project_id=proj_id)
            _p(
                "project + chat in project",
                {"project": proj_id, "conversation": r["conversation_id"], "text": text_of(r)[:80]},
            )
            t = turns(c, r["conversation_id"])
            _p(
                "turns",
                [{"seq": x["seq"], "role": x["role"], "status": x["status"]} for x in t["turns"]],
            )
            _p("list_conversations(project)", list_conversations(c, project_id=proj_id)["total"])
        except ChatEngineError as e:
            failures += 1
            _p("memory ERROR", str(e))

    if want("skills"):
        sid_ = f"ref_skill_{uuid.uuid4().hex[:6]}"
        try:
            register_skill(c, sid_, "Reference skill", "Always end your answer with the word DONE.")
            if proj_id:
                assign_skill(c, "projects", proj_id, sid_)
                r = chat(c, "قل مرحبا.", project_id=proj_id)
                _p(
                    "skill applied (skills_used)",
                    {"skills_used": r["turn"]["skills_used"], "text": text_of(r)[:60]},
                )
            prof = create_agent_profile(
                c, "reference-profile", system_instructions="Be terse.", skills=[{"skill_id": sid_}]
            )
            _p("agent_profile", {"id": prof["id"], "skills": prof["skills"]})
            delete_agent_profile(c, prof["id"])
        except ChatEngineError as e:
            failures += 1
            _p("skills ERROR", str(e))

    if want("openai"):
        try:
            r = chat_completions(c, "deepseek-v3.2", [{"role": "user", "content": "Say OK"}])
            assert isinstance(r, dict)
            _p(
                "chat_completions sync",
                {
                    "content": r["choices"][0]["message"]["content"],
                    "chat_engine": r.get("chat_engine"),
                },
            )
            chunks = list(
                chat_completions(
                    c, "deepseek-v3.2", [{"role": "user", "content": "Say OK"}], stream=True
                )
            )  # type: ignore[arg-type]
            last = chunks[-1]["choices"][0].get("finish_reason") if chunks else None
            _p(
                "chat_completions stream (gw_* is sync-only ⇒ finish_reason 'error' is the honest answer here)",
                {"chunks": len(chunks), "last_finish": last},
            )
        except ChatEngineError as e:
            failures += 1
            _p("openai ERROR", str(e))

    if want("execute"):
        try:
            r = execute(c, "Say OK", idempotency_key=f"ref-{uuid.uuid4().hex[:8]}")
            _p("execute", {"status": r.get("execution", {}).get("status")})
        except ChatEngineError as e:
            # upstream surface: the auto-picked provider may refuse (403 entitlement / 502) — honest, not a client bug
            _p("execute (upstream refused — expected when the auto pick has no quota)", str(e))

    if want("admin"):
        try:
            _p(
                "admin_providers (keys)",
                [p["provider_key"] for p in admin_providers(c)["providers"]][:16],
            )
            key = f"ref_draft_{uuid.uuid4().hex[:5]}"
            admin_provider_draft(
                c, key, "Reference draft", "http://127.0.0.1:9/v1", static_models=["m1"]
            )
            _p(
                "admin_provider_draft → :validate (unreachable base_url, honest)",
                admin_provider_transition(c, key, "validate"),
            )
            admin_provider_transition(c, key, "archive")
            cred = create_credential(
                c, "reference-cred", "sk-reference-000000", provider_hint="gw_deepai"
            )
            _p(
                "create_credential (no secret echoed)",
                {k: cred.get(k) for k in ("id", "credential_ref", "fingerprint", "last4")},
            )
            delete_credential(c, cred["id"])
            _p("usage_records (count)", len(usage_records(c).get("records", [])))
        except ChatEngineError as e:
            failures += 1
            _p("admin ERROR", str(e))

    if want("errors"):
        for label, fn in (
            ("422 extra field", lambda: c.call("POST", "/v1/chat", {"message": "x", "bogus": 1})),
            ("404 unknown conversation", lambda: chat(c, "x", conversation_id=str(uuid.uuid4()))),
            (
                "422 sequential_handoff <2 stages",
                lambda: chat_sequential_handoff(c, "x", ["deepseek-v3.2"]),
            ),
        ):
            try:
                fn()
                failures += 1
                _p(label, "UNEXPECTED SUCCESS")
            except ChatEngineError as e:
                _p(label, f"{e.status} {e.code}: {e.message}")

    # cleanup
    if proj_id:
        for cv in list_conversations(c, project_id=proj_id).get("conversations", []):
            delete_conversation(c, cv["id"])
        delete_project(c, proj_id)
    if conv_id:
        delete_conversation(c, conv_id)
    print(f"\nTOUR DONE failures={failures}")
    return 1 if failures else 0


SECTIONS = (
    "discovery",
    "chat",
    "stream",
    "strategies",
    "memory",
    "skills",
    "openai",
    "execute",
    "admin",
    "errors",
)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--base-url", help="override endpoint (else CHAT_ENGINE_URL, else config/endpoint.json)"
    )
    ap.add_argument("--list", action="store_true", help="list every reference request and exit")
    ap.add_argument("--only", choices=SECTIONS, help="run one tour section")
    a = ap.parse_args()
    if a.list:
        import inspect

        skip = {"main", "tour", "text_of", "collect_stream"}
        for name, fn in sorted(globals().items()):
            if (
                inspect.isfunction(fn)
                and fn.__module__ == __name__
                and not name.startswith("_")
                and fn.__doc__
                and name not in skip
            ):
                print(f"{name:28} {fn.__doc__.strip().splitlines()[0]}")
        return 0
    cfg = Config.load(a.base_url)
    print(f"endpoint: {cfg.base_url}  (config: {CONFIG_PATH})")
    return tour(Client(cfg), a.only)


if __name__ == "__main__":
    sys.exit(main())
