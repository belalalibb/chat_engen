"""Built-in probes (CE-06). Each returns (passed, detail, latency_ms, evidence).

Probes run ONLY on admin request (never on a timer) and spend at most one tiny
generation each — a daily cap per provider keeps live spend bounded.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import uuid4

from core.contracts.base import JsonObject
from core.contracts.provider import ProviderGenerateRequest, ProviderOperation

if TYPE_CHECKING:
    from chat_engine.composition.runtime import Runtime

ProbeFn = Callable[[str, str | None], Awaitable[tuple[bool, str | None, int | None, JsonObject]]]
DAILY_PROBE_CAP = 50


class ProbeBudgetExceeded(RuntimeError):
    pass


def _adapter_and_model(rt: Runtime, provider_key: str, model_key: str | None) -> tuple[object, str, str]:
    entry = rt.providers.get(provider_key)
    adapter = rt.adapters[entry.provider.id]
    bindings = rt.bindings.bindings_for_provider(entry.provider.id)
    if not bindings:
        msg = f"{provider_key}: no bound models to probe"
        raise LookupError(msg)
    binding = bindings[0]
    if model_key is not None:
        model = rt.models.get(model_key)
        binding = next((b for b in bindings if b.model_id == model.id), binding)
    return adapter, binding.provider_model_name, rt.credential_refs[entry.provider.id]


def make_generation_probe(rt: Runtime, *, capability: str, payload_extra: JsonObject | None = None, check: Callable[[JsonObject], bool] | None = None) -> ProbeFn:
    counter: dict[str, int] = {}

    async def _probe(provider_key: str, model_key: str | None) -> tuple[bool, str | None, int | None, JsonObject]:
        day = time.strftime("%Y-%m-%d")
        k = f"{provider_key}:{day}"
        counter[k] = counter.get(k, 0) + 1
        if counter[k] > DAILY_PROBE_CAP:
            raise ProbeBudgetExceeded(f"{provider_key}: daily probe cap ({DAILY_PROBE_CAP}) reached")
        adapter, provider_model_name, credential_ref = _adapter_and_model(rt, provider_key, model_key)
        payload: JsonObject = {"ask": "Reply with the single word: ok", "generation": {"max_tokens": 16, "temperature": 0}}
        payload.update(payload_extra or {})
        req = ProviderGenerateRequest(request_id=uuid4(), tenant_id=rt.tenant_id, operation=ProviderOperation.GENERATE_TEXT, provider_model_name=provider_model_name, credential_ref=credential_ref, payload=payload, timeout_ms=30_000)
        resp = await adapter.generate(req)  # type: ignore[attr-defined]
        evidence: JsonObject = {"capability": capability, "model": provider_model_name, "succeeded": resp.succeeded, "usage": dict(resp.usage)}
        if not resp.succeeded:
            err = resp.error
            return False, f"{err.category.value}: {err.safe_message}" if err else "failed", resp.latency_ms, evidence
        ok = check(resp.output) if check else True
        evidence["output_keys"] = sorted(resp.output)
        return ok, None if ok else "output did not satisfy the capability check", resp.latency_ms, evidence

    return _probe


def register_default_probes(rt: Runtime, ledger: object) -> None:
    from chat_engine.capabilities.ledger import CapabilityLedger

    assert isinstance(ledger, CapabilityLedger)
    ledger.register_probe("chat", make_generation_probe(rt, capability="chat"))
    ledger.register_probe("multi_turn", make_generation_probe(rt, capability="multi_turn", payload_extra={"messages": [{"role": "user", "content": "Remember the word: pineapple"}, {"role": "assistant", "content": "Noted."}, {"role": "user", "content": "Reply with the single word: ok"}]}))
    ledger.register_probe("system_prompt", make_generation_probe(rt, capability="system_prompt", payload_extra={"messages": [{"role": "system", "content": "You answer with exactly one word."}, {"role": "user", "content": "Reply with the single word: ok"}]}))
    ledger.register_probe(
        "json_mode",
        make_generation_probe(rt, capability="json_mode", payload_extra={"json_mode": True, "ask": 'Return JSON {"ok": true}'}, check=lambda out: _is_json(out.get("content"))),
    )
    ledger.register_probe(
        "structured_output",
        make_generation_probe(rt, capability="structured_output", payload_extra={"response_schema": {"name": "ok", "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}}, "ask": "Return the object."}, check=lambda out: _is_json(out.get("content"))),
    )
    ledger.register_probe(
        "tool_calling",
        make_generation_probe(rt, capability="tool_calling", payload_extra={"tools": [{"type": "function", "function": {"name": "ping", "parameters": {"type": "object", "properties": {}}}}], "tool_choice": "auto", "ask": "Call the ping tool."}, check=lambda out: bool(out.get("tool_calls")) or "ping" in str(out.get("content", ""))),
    )


def _is_json(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        json.loads(value)
    except ValueError:
        return False
    return True
