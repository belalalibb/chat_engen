"""CE-03: HandoffPacket → messages[] — golden handoff, truncation, legacy bridge."""

from __future__ import annotations

from core.contracts.context import ComposedContext, ContextBlock, ContextBlockType
from core.contracts.provider import ProviderGenerateRequest, ProviderOperation
from uuid import uuid4

from chat_engine.handoff import (
    CanonicalMessage,
    CanonicalRole,
    Decision,
    HandoffBudget,
    HandoffCompiler,
    HandoffPacket,
    ToolResult,
    ToolResultPart,
)


def _packet(n_turns: int = 4, **kw) -> HandoffPacket:
    turns = []
    for i in range(n_turns):
        role = CanonicalRole.USER if i % 2 == 0 else CanonicalRole.ASSISTANT
        turns.append(CanonicalMessage.of(role, f"turn {i} " + "x" * 200, source=f"turn:{i}"))
    turns.append(CanonicalMessage.of("user", "final question"))
    return HandoffPacket(packet_id="p1", messages=turns, **kw)


def test_three_stage_handoff_carries_everything_and_no_tool_rerun() -> None:
    packet = _packet(
        objective="Ship feature X",
        constraints=["no breaking changes", "Arabic docs"],
        decisions=[Decision(key="db", value="postgres", reason="ADR-0002")],
        tool_results=[ToolResult(tool_call_id="c1", name="search", result={"hits": 3})],
    )
    out = HandoffCompiler().compile(packet)
    system = out.messages[0]
    assert system["role"] == "system"
    assert "Ship feature X" in system["content"]
    assert "no breaking changes" in system["content"] and "Arabic docs" in system["content"]
    assert "db: postgres" in system["content"]
    tools = [m for m in out.messages if m["role"] == "tool"]
    assert len(tools) == 1 and tools[0]["tool_call_id"] == "c1"
    assert out.messages[-1] == {"role": "user", "content": "final question"}
    assert out.truncated is False and out.dropped_messages == 0
    # tool result already in history ⇒ not injected twice
    packet2 = packet.model_copy(
        update={
            "messages": [
                *packet.messages[:-1],
                CanonicalMessage(
                    role=CanonicalRole.TOOL,
                    parts=[ToolResultPart(tool_call_id="c1", name="search", result={"hits": 3})],
                ),
                packet.messages[-1],
            ]
        }
    )
    out2 = HandoffCompiler().compile(packet2)
    assert len([m for m in out2.messages if m["role"] == "tool"]) == 1


def test_truncation_drops_oldest_history_but_never_constraints_or_final_turn() -> None:
    packet = _packet(
        n_turns=50,
        constraints=["keep me"],
        budget=HandoffBudget(context_window=1500, reserve_for_output=200),
    )
    out = HandoffCompiler().compile(packet)
    assert out.truncated and out.dropped_messages > 0
    assert "keep me" in out.messages[0]["content"]
    assert out.messages[-1]["content"] == "final question"
    kept_sources = [m["content"][:8] for m in out.messages[1:-1]]
    # newest turns survive (chronological order preserved)
    assert kept_sources == sorted(kept_sources, key=lambda s: int(s.split()[1]))
    assert out.estimated_tokens <= 1500


def test_tool_results_survive_truncation() -> None:
    packet = _packet(
        n_turns=40,
        tool_results=[ToolResult(tool_call_id="t9", name="calc", result="42")],
        budget=HandoffBudget(context_window=600, reserve_for_output=100),
    )
    out = HandoffCompiler().compile(packet)
    assert any(m.get("tool_call_id") == "t9" for m in out.messages)


def test_legacy_bridge_from_composed_context() -> None:
    composed = ComposedContext(
        context_blocks=[
            ContextBlock(type=ContextBlockType.ROLE, content="Be terse", source="role:x"),
            ContextBlock(type=ContextBlockType.HISTORY, content="user: hi", source="message:1"),
            ContextBlock(type=ContextBlockType.HISTORY, content="assistant: hello", source="message:2"),
            ContextBlock(type=ContextBlockType.ASK, content="now what?", source="request"),
        ]
    )
    msgs = HandoffCompiler().messages_from_composed(composed)
    assert msgs == [
        {"role": "system", "content": "Be terse"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "now what?"},
    ]


def test_legacy_adapter_ignores_messages_key() -> None:
    """ProviderGenerateRequest is a frozen upstream contract; payload is JsonObject,
    so adding ``messages`` is additive — the echo adapter (byte-identical to
    upstream's) keeps answering from ``ask`` and never surfaces the new key."""
    from chat_engine.composition.runtime import LocalEchoAdapter, echo_manifest
    import asyncio

    req = ProviderGenerateRequest(
        request_id=uuid4(),
        tenant_id=uuid4(),
        operation=ProviderOperation.GENERATE_TEXT,
        provider_model_name="local-echo-1",
        credential_ref="secret-ref://x",
        payload={"ask": "hi", "messages": [{"role": "user", "content": "hi"}]},
    )
    resp = asyncio.run(LocalEchoAdapter(echo_manifest()).generate(req))
    assert resp.output["echo"] == "hi"
    assert "messages" not in resp.output and "messages_received" not in resp.output
