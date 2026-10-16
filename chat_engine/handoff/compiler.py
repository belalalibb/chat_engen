"""HandoffCompiler — HandoffPacket → OpenAI-compatible ``messages[]`` (D4).

Rules (recorded):
- One system message carries objective + constraints + decisions + progress +
  summary. These are MANDATORY: they are never truncated before history.
- ``tool_results`` not already present in history become ``tool`` messages
  placed before the latest user turn — a model never re-runs a done tool.
- Truncation drops the OLDEST history messages first, never the final user
  turn, never system. The result says exactly how many were dropped.
- Token estimate is a documented heuristic (chars/4) until per-model
  tokenizers exist; ``budget.max_tokens_hint`` lets the caller tighten it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from core.contracts.base import JsonObject
from core.contracts.context import ComposedContext

from chat_engine.handoff.contracts import (
    CanonicalMessage,
    CanonicalRole,
    CompiledHandoff,
    FileRefPart,
    HandoffPacket,
    ImageRefPart,
    TextPart,
    ToolCallPart,
    ToolResultPart,
)

CHARS_PER_TOKEN = 4
SYSTEM_HEADER = "You are continuing an in-progress task. Follow the handoff below exactly."


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN) if text else 0


def _message_tokens(message: JsonObject) -> int:
    content = message.get("content")
    if isinstance(content, str):
        return estimate_tokens(content) + 4
    return estimate_tokens(json.dumps(message, sort_keys=True)) + 4


def _system_text(packet: HandoffPacket) -> str:
    lines: list[str] = [SYSTEM_HEADER]
    if packet.objective:
        lines += ["", "## Objective", packet.objective]
    if packet.constraints:
        lines += ["", "## Constraints (binding)"] + [f"- {c}" for c in packet.constraints]
    if packet.decisions:
        lines += ["", "## Decisions already taken (do not re-decide)"]
        for d in packet.decisions:
            suffix = f" — {d.reason}" if d.reason else ""
            lines.append(f"- {d.key}: {d.value}{suffix}")
    if packet.progress.done or packet.progress.pending or packet.progress.resume_at:
        lines += ["", "## Progress"]
        if packet.progress.done:
            lines.append("Done: " + "; ".join(packet.progress.done))
        if packet.progress.pending:
            lines.append("Pending: " + "; ".join(packet.progress.pending))
        if packet.progress.resume_at:
            lines.append("Resume at: " + packet.progress.resume_at)
    if packet.summary:
        lines += ["", "## Summary of earlier conversation", packet.summary]
    if packet.attachments:
        lines += ["", "## Attachments (references)"] + [
            f"- {a.name or a.ref} ({a.mime_type or 'unknown'})" for a in packet.attachments
        ]
    if packet.output_spec is not None:
        spec = packet.output_spec.model_dump(mode="json", by_alias=True, exclude_none=True)
        if spec:
            lines += ["", "## Output requirements", json.dumps(spec, sort_keys=True)]
    if packet.skills:
        lines += ["", "## Skills in effect"] + [
            f"- {s.get('id')}@{s.get('version')}" for s in packet.skills
        ]
        for s in packet.skills:
            body = s.get("instructions")
            if isinstance(body, str) and body.strip():
                lines += ["", f"### Skill: {s.get('name') or s.get('id')}", body.strip()]
    return "\n".join(lines)


def _openai_message(message: CanonicalMessage) -> JsonObject:
    """Project one canonical message to the OpenAI chat shape."""
    text_parts = [p for p in message.parts if isinstance(p, TextPart)]
    media = [p for p in message.parts if isinstance(p, ImageRefPart | FileRefPart)]
    tool_calls = [p for p in message.parts if isinstance(p, ToolCallPart)]
    tool_results = [p for p in message.parts if isinstance(p, ToolResultPart)]

    if message.role is CanonicalRole.TOOL or (tool_results and not text_parts):
        result = tool_results[0] if tool_results else None
        content = (
            (
                result.result
                if isinstance(result.result, str)
                else json.dumps(result.result, sort_keys=True)
            )
            if result
            else message.text()
        )
        out: JsonObject = {
            "role": "tool",
            "tool_call_id": (result.tool_call_id if result else message.tool_call_id) or "",
            "content": content,
        }
        if result is not None:
            out["name"] = result.name
        return out

    out = {"role": message.role.value}
    if media:
        parts: list[JsonObject] = [{"type": "text", "text": p.text} for p in text_parts]
        for m in media:
            if isinstance(m, ImageRefPart):
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": m.ref, **({"detail": m.detail} if m.detail else {})},
                    }
                )
            else:
                parts.append({"type": "text", "text": f"[attachment: {m.name or m.ref}]"})
        out["content"] = parts
    else:
        out["content"] = "".join(p.text for p in text_parts)
    if message.name:
        out["name"] = message.name
    if tool_calls:
        out["tool_calls"] = [
            {
                "id": c.tool_call_id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments, sort_keys=True)},
            }
            for c in tool_calls
        ]
    return out


class HandoffCompiler:
    def __init__(self, *, chars_per_token: int = CHARS_PER_TOKEN) -> None:
        self._cpt = chars_per_token

    # -- the main projection ---------------------------------------------------------

    def compile(self, packet: HandoffPacket) -> CompiledHandoff:
        notes: list[str] = []
        system = {"role": "system", "content": _system_text(packet)}

        history = [_openai_message(m) for m in packet.messages]
        seen_tool_ids = {m.get("tool_call_id") for m in history if m.get("role") == "tool"}
        pending_tools: list[JsonObject] = [
            {
                "role": "tool",
                "tool_call_id": tr.tool_call_id,
                "name": tr.name,
                "content": tr.result
                if isinstance(tr.result, str)
                else json.dumps(tr.result, sort_keys=True),
            }
            for tr in packet.tool_results
            if tr.tool_call_id not in seen_tool_ids
        ]
        if pending_tools:
            notes.append(f"{len(pending_tools)} tool_result(s) injected from packet")

        # Keep the final user turn pinned at the end; tool results go before it.
        tail: list[JsonObject] = []
        if history and history[-1].get("role") == "user":
            tail = [history.pop()]
        body = history + pending_tools

        mandatory_tokens = _message_tokens(system) + sum(_message_tokens(m) for m in tail)
        limit = self._limit(packet)
        dropped = 0
        if limit is not None:
            available = limit - mandatory_tokens - packet.budget.reserve_for_output
            kept: list[JsonObject] = []
            used = 0
            # newest-first fill, then restore chronological order
            for m in reversed(body):
                cost = _message_tokens(m)
                if m.get("role") == "tool" and m in pending_tools:
                    kept.append(m)  # tool results are mandatory
                    used += cost
                    continue
                if used + cost > available:
                    dropped += 1
                    continue
                kept.append(m)
                used += cost
            body = list(reversed(kept))
            if dropped:
                notes.append(f"dropped {dropped} oldest history message(s) to fit context window")

        messages = [system, *body, *tail]
        total = sum(_message_tokens(m) for m in messages)
        return CompiledHandoff(
            messages=messages,
            estimated_tokens=total,
            dropped_messages=dropped,
            truncated=dropped > 0,
            notes=notes,
        )

    def _limit(self, packet: HandoffPacket) -> int | None:
        candidates = [v for v in (packet.budget.context_window, packet.budget.max_tokens_hint) if v]
        return min(candidates) if candidates else None

    # -- legacy bridge (CE-02 /v1/execute) -------------------------------------------------

    def messages_from_composed(self, composed: ComposedContext) -> list[JsonObject]:
        """Upstream's ComposedContext (role/memory/history/ask blocks) → messages[].

        Additive bridge for /v1/execute: adapters that understand ``messages``
        get real turns; legacy adapters keep reading ``ask``/``context``.
        """
        messages: list[JsonObject] = []
        system_bits: list[str] = []
        for block in composed.context_blocks:
            kind = block.type.value
            if kind == "role":
                system_bits.append(block.content)
            elif kind == "memory":
                system_bits.append(f"Known preference/fact: {block.content}")
            elif kind == "history":
                role = "assistant" if block.source.startswith("message:assistant") else "user"
                text = block.content
                for prefix, r in (("assistant: ", "assistant"), ("user: ", "user")):
                    if text.lower().startswith(prefix):
                        role, text = r, text[len(prefix) :]
                        break
                messages.append({"role": role, "content": text})
            elif kind == "ask":
                messages.append({"role": "user", "content": block.content})
        if system_bits:
            messages.insert(0, {"role": "system", "content": "\n".join(system_bits)})
        return messages


def packet_from_turns(
    *,
    packet_id: str,
    turns: Iterable[CanonicalMessage],
    objective: str = "",
    constraints: list[str] | None = None,
    summary: str | None = None,
    context_window: int | None = None,
) -> HandoffPacket:
    from chat_engine.handoff.contracts import HandoffBudget

    return HandoffPacket(
        packet_id=packet_id,
        objective=objective,
        constraints=list(constraints or []),
        messages=list(turns),
        summary=summary,
        budget=HandoffBudget(context_window=context_window),
    )
