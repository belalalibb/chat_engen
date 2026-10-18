"""StreamService (ADR-CE-0001 D7) — routes via the ONE Router, streams via the
optional ``StreamingAdapterPort``, and enforces the five-case semantics:

1. provider supports streaming        → token events
2. provider does not                  → 422 unsupported_capability, or with
                                        ``stream_fallback=buffer`` one synthesized
                                        burst tagged ``synthesized: true``
3. failure BEFORE first delta         → silent failover to the next candidate
4. failure AFTER first delta          → ``failed{reason: stream_interrupted, resumable}``
                                        — NEVER a silent failover (no duplicated text)
5. client cancel                      → ``cancelled`` + upstream cancel + partial settle

Timeouts: first-byte / idle / total. Heartbeats on idle. Backpressure: a
bounded queue slows the producer. Resume: ``delivered_until_seq`` + the stored
partial lets a client continue without re-receiving delivered text.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from core.contracts.base import JsonObject
from core.contracts.provider import ProviderGenerateRequest, ProviderOperation
from core.contracts.routing import CandidateScore, RoutingDecision

from chat_engine.streaming.contracts import (
    StreamChunk,
    StreamEvent,
    StreamEventType,
    StreamFailureReason,
    StreamingAdapterPort,
    StreamSummary,
    StreamTimeouts,
)
from chat_engine.util.time import utc_now

if TYPE_CHECKING:
    from chat_engine.composition.runtime import Runtime

QUEUE_MAX = 256  # backpressure bound


@dataclass
class StreamState:
    stream_id: str
    summary: StreamSummary
    events: list[StreamEvent] = field(default_factory=list)
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    payload: JsonObject = field(default_factory=dict)
    decision: RoutingDecision | None = None
    tried: list[UUID] = field(default_factory=list)  # provider ids already used
    partial_tool_calls: dict[int, dict[str, Any]] = field(default_factory=dict)


class StreamService:
    def __init__(self, rt: Runtime, *, timeouts: StreamTimeouts | None = None) -> None:
        self._rt = rt
        self._timeouts = timeouts or StreamTimeouts()
        self._streams: dict[str, StreamState] = {}

    # -- registry --------------------------------------------------------------------------

    def get(self, stream_id: str) -> StreamState | None:
        return self._streams.get(stream_id)

    def cancel(self, stream_id: str) -> bool:
        st = self._streams.get(stream_id)
        if st is None:
            return False
        st.cancel.set()
        return True

    def supports_streaming(self, provider_id: UUID) -> bool:
        adapter = self._rt.adapters.get(provider_id)
        if adapter is None:
            return False
        if not isinstance(adapter, StreamingAdapterPort):
            return False
        ledger = self._rt.extensions.get("capabilities")
        pk = self._rt.extensions["catalog_index"].provider_key(provider_id) or ""
        if ledger is not None:
            verdict = ledger.gate(pk, "streaming_text")
            if not verdict.allowed:
                return False
        return True

    # -- main entry --------------------------------------------------------------------------

    async def run(
        self,
        *,
        decision: RoutingDecision,
        payload: JsonObject,
        timeout_ms: int | None = None,
        stream_fallback: str | None = None,
        resume_from: StreamState | None = None,
    ) -> AsyncIterator[StreamEvent]:
        stream_id = resume_from.stream_id if resume_from else str(uuid4())
        st = resume_from or StreamState(
            stream_id=stream_id,
            summary=StreamSummary(stream_id=stream_id, status=StreamEventType.STARTED),
            payload=dict(payload),
            decision=decision,
        )
        self._streams[stream_id] = st
        seq = st.summary.delivered_seq

        def ev(t: StreamEventType, data: JsonObject | None = None) -> StreamEvent:
            nonlocal seq
            e = StreamEvent(
                seq=seq, event_id=f"{stream_id}:{seq}", type=t, stream_id=stream_id, data=data or {}
            )
            seq += 1
            st.events.append(e)
            st.summary = st.summary.model_copy(
                update={
                    "delivered_seq": seq,
                    "status": t
                    if t
                    in (
                        StreamEventType.COMPLETED,
                        StreamEventType.FAILED,
                        StreamEventType.CANCELLED,
                    )
                    else st.summary.status,
                }
            )
            return e

        candidates: list[CandidateScore] = [decision.selected, *decision.fallback_candidates]
        candidates = [c for c in candidates if c.provider_id not in st.tried]
        if not candidates:
            yield ev(
                StreamEventType.FAILED,
                {"reason": StreamFailureReason.NO_CANDIDATE.value, "resumable": False},
            )
            return

        admission = self._rt.extensions.get("admission")
        index = self._rt.extensions["catalog_index"]
        first_delta_seen = False
        failovers = st.summary.failovers

        if not resume_from:
            yield ev(
                StreamEventType.STARTED,
                {"candidates": len(candidates), "timeouts": self._timeouts.model_dump()},
            )

        for cand in candidates:
            pk = index.provider_key(cand.provider_id) or "?"
            model = self._rt.models.get_by_id(cand.model_id)
            binding = self._rt.bindings.get(cand.provider_id, cand.model_id)
            adapter = self._rt.adapters.get(cand.provider_id)
            st.tried.append(cand.provider_id)
            st.summary = st.summary.model_copy(
                update={"provider_key": pk, "model_key": model.model_key}
            )

            # case 2: not streamable
            if not self.supports_streaming(cand.provider_id):
                if stream_fallback == "buffer" and adapter is not None:
                    buffered_ok = False
                    async for e in self._buffered(
                        st,
                        ev,
                        adapter,
                        cand,
                        binding.provider_model_name,
                        timeout_ms,
                        is_last=cand is candidates[-1],
                    ):
                        if e.type is StreamEventType.COMPLETED:
                            buffered_ok = True
                        if e.type is StreamEventType.CANCELLED:
                            buffered_ok = True  # terminal: do not fail over after a cancel
                        if e.type is StreamEventType.FAILED and not e.data.get("final"):
                            continue  # provider failover: try the next candidate
                        yield e
                    if buffered_ok or cand is candidates[-1]:
                        return
                    failovers += 1
                    st.summary = st.summary.model_copy(update={"failovers": failovers})
                    continue
                if cand is candidates[-1] and not first_delta_seen:
                    yield ev(
                        StreamEventType.FAILED,
                        {
                            "reason": StreamFailureReason.UNSUPPORTED.value,
                            "resumable": False,
                            "provider_key": pk,
                            "hint": "set stream_fallback=buffer or choose a provider with verified streaming_text",
                        },
                    )
                    return
                failovers += 1
                continue

            reservation = None
            if admission is not None:
                from chat_engine.admission import Denied

                got = await admission.reserve(
                    [f"tenant:{self._rt.tenant_id}", f"provider:{pk}"],
                    tokens_estimate=self._estimate(st.payload),
                    units=1.0,
                )
                if isinstance(got, Denied):
                    if cand is candidates[-1] and not first_delta_seen:
                        yield ev(
                            StreamEventType.FAILED,
                            {
                                "reason": StreamFailureReason.ADMISSION_DENIED.value,
                                "resumable": True,
                                "retry_after_ms": got.retry_after_ms,
                                "scope": got.scope,
                            },
                        )
                        return
                    failovers += 1
                    continue
                reservation = got

            req = ProviderGenerateRequest(
                request_id=uuid4(),
                tenant_id=self._rt.tenant_id,
                operation=ProviderOperation.GENERATE_TEXT,
                provider_model_name=binding.provider_model_name,
                credential_ref=self._rt.credential_refs.get(cand.provider_id, ""),
                payload=st.payload,
                timeout_ms=timeout_ms,
            )
            assert adapter is not None
            outcome, usage = await self._pump(st, ev, adapter, req, None)
            # _pump yields through a queue; collect its events
            async for e in self._drain(st, ev, outcome):
                if e.type is StreamEventType.MESSAGE_DELTA or e.type in (
                    StreamEventType.TOOL_CALL_START,
                    StreamEventType.TOOL_CALL_DELTA,
                ):
                    first_delta_seen = True
                yield e
            result = outcome["result"]
            if admission is not None and reservation is not None:
                total = usage.get("total_tokens") if isinstance(usage, dict) else None
                await admission.settle(
                    reservation,
                    tokens_actual=total if isinstance(total, int) else None,
                    success=result == "completed",
                )
            if result == "completed":
                st.summary = st.summary.model_copy(
                    update={"failovers": failovers, "usage": dict(usage)}
                )
                yield ev(
                    StreamEventType.COMPLETED,
                    {
                        "provider_key": pk,
                        "model_key": model.model_key,
                        "usage": dict(usage),
                        "failovers": failovers,
                        "text_length": len(st.summary.text),
                    },
                )
                return
            if result == "cancelled":
                yield ev(
                    StreamEventType.CANCELLED,
                    {
                        "provider_key": pk,
                        "delivered_until_seq": seq,
                        "partial_text_length": len(st.summary.text),
                    },
                )
                return
            # result == failed
            if first_delta_seen or st.summary.text:
                # case 4: never silent after first delta
                st.summary = st.summary.model_copy(
                    update={
                        "resumable": True,
                        "reason": StreamFailureReason.STREAM_INTERRUPTED,
                        "failovers": failovers,
                    }
                )
                yield ev(
                    StreamEventType.FAILED,
                    {
                        "reason": StreamFailureReason.STREAM_INTERRUPTED.value,
                        "resumable": True,
                        "provider_key": pk,
                        "delivered_until_seq": seq,
                        "detail": outcome.get("detail"),
                        "resume_url": f"/v1/chat/stream/{stream_id}:resume",
                    },
                )
                return
            # case 3: before first delta → failover
            failovers += 1
            self._rt.signals.record_error(
                provider_id=cand.provider_id, model_id=cand.model_id, error=outcome["error"]
            ) if outcome.get("error") is not None else None
            continue

        yield ev(
            StreamEventType.FAILED,
            {
                "reason": StreamFailureReason.PROVIDER_ERROR.value,
                "resumable": False,
                "failovers": failovers,
                "detail": "all candidates failed before producing output",
            },
        )

    # -- internals -----------------------------------------------------------------------------

    @staticmethod
    def _estimate(payload: JsonObject) -> int:
        msgs = payload.get("messages")
        if isinstance(msgs, list):
            return sum(len(str(m.get("content", ""))) for m in msgs if isinstance(m, dict)) // 4
        return len(str(payload.get("ask", ""))) // 4

    async def _pump(
        self, st: StreamState, ev: Any, adapter: Any, req: ProviderGenerateRequest, _first: Any
    ) -> tuple[dict[str, Any], JsonObject]:
        """Start the producer task feeding a bounded queue; return the outcome holder."""
        queue: asyncio.Queue[StreamChunk | None] = asyncio.Queue(maxsize=QUEUE_MAX)
        outcome: dict[str, Any] = {
            "result": "failed",
            "queue": queue,
            "task": None,
            "detail": None,
            "error": None,
        }
        usage: JsonObject = {}
        outcome["usage"] = usage

        async def produce() -> None:
            try:
                async for chunk in adapter.stream(req):
                    await queue.put(chunk)
                    if st.cancel.is_set():
                        return
            except Exception as exc:
                await queue.put(StreamChunk(error=adapter.normalize_error(exc)))
            finally:
                await queue.put(None)

        outcome["task"] = asyncio.create_task(produce())
        return outcome, usage

    async def _drain(
        self, st: StreamState, ev: Any, outcome: dict[str, Any]
    ) -> AsyncIterator[StreamEvent]:
        queue: asyncio.Queue[StreamChunk | None] = outcome["queue"]
        task: asyncio.Task[None] = outcome["task"]
        usage: JsonObject = outcome["usage"]
        t = self._timeouts
        started = asyncio.get_event_loop().time()
        got_first = False
        finish_reason: str | None = None
        try:
            while True:
                wait = (t.first_byte_ms if not got_first else t.idle_ms) / 1000
                if t.total_ms is not None:
                    remaining = t.total_ms / 1000 - (asyncio.get_event_loop().time() - started)
                    if remaining <= 0:
                        outcome["result"], outcome["detail"] = (
                            "failed",
                            StreamFailureReason.DEADLINE_EXCEEDED.value,
                        )
                        return
                    wait = min(wait, remaining)
                hb = t.heartbeat_ms / 1000
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=min(wait, hb))
                except TimeoutError:
                    if st.cancel.is_set():
                        outcome["result"] = "cancelled"
                        return
                    elapsed_idle = min(wait, hb)
                    if elapsed_idle >= wait:
                        outcome["result"], outcome["detail"] = (
                            "failed",
                            (
                                StreamFailureReason.FIRST_BYTE_TIMEOUT.value
                                if not got_first
                                else StreamFailureReason.IDLE_TIMEOUT.value
                            ),
                        )
                        return
                    yield ev(StreamEventType.HEARTBEAT, {"idle_ms": int(elapsed_idle * 1000)})
                    continue
                if st.cancel.is_set():
                    outcome["result"] = "cancelled"
                    return
                if item is None:
                    outcome["result"] = "completed" if (got_first or finish_reason) else "failed"
                    if outcome["result"] == "failed":
                        outcome["detail"] = "stream ended without output"
                    return
                if item.error is not None:
                    outcome["result"], outcome["detail"], outcome["error"] = (
                        "failed",
                        f"{item.error.category.value}: {item.error.safe_message}",
                        item.error,
                    )
                    return
                if item.delta:
                    got_first = True
                    st.summary = st.summary.model_copy(
                        update={"text": st.summary.text + item.delta}
                    )
                    yield ev(StreamEventType.MESSAGE_DELTA, {"delta": item.delta})
                if item.tool_call is not None:
                    got_first = True
                    idx = int(item.tool_call.get("index", 0))
                    cur = st.partial_tool_calls.get(idx)
                    if cur is None:
                        cur = {
                            "index": idx,
                            "id": item.tool_call.get("id"),
                            "name": item.tool_call.get("name"),
                            "arguments": "",
                        }
                        st.partial_tool_calls[idx] = cur
                        yield ev(
                            StreamEventType.TOOL_CALL_START,
                            {"index": idx, "id": cur["id"], "name": cur["name"]},
                        )
                    if item.tool_call.get("name") and not cur.get("name"):
                        cur["name"] = item.tool_call["name"]
                    piece = item.tool_call.get("arguments_delta") or ""
                    if piece:
                        cur["arguments"] += piece
                        yield ev(
                            StreamEventType.TOOL_CALL_DELTA,
                            {"index": idx, "arguments_delta": piece},
                        )
                if item.usage:
                    usage.update(item.usage)
                    yield ev(StreamEventType.USAGE, dict(item.usage))
                if item.finish_reason:
                    finish_reason = item.finish_reason
                    for idx, cur in st.partial_tool_calls.items():
                        yield ev(
                            StreamEventType.TOOL_CALL_END,
                            {
                                "index": idx,
                                "id": cur["id"],
                                "name": cur["name"],
                                "arguments": cur["arguments"],
                            },
                        )
                    st.summary = st.summary.model_copy(
                        update={"tool_calls": list(st.partial_tool_calls.values())}
                    )
                    st.partial_tool_calls = {}
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def _buffered(
        self,
        st: StreamState,
        ev: Any,
        adapter: Any,
        cand: CandidateScore,
        provider_model_name: str,
        timeout_ms: int | None,
        *,
        is_last: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        """Case 2 with explicit policy: one sync generate, one burst, honestly tagged.

        A provider failure yields FAILED with ``final`` = ``is_last``; the caller
        swallows non-final failures and fails over to the next candidate."""
        req = ProviderGenerateRequest(
            request_id=uuid4(),
            tenant_id=self._rt.tenant_id,
            operation=ProviderOperation.GENERATE_TEXT,
            provider_model_name=provider_model_name,
            credential_ref=self._rt.credential_refs.get(cand.provider_id, ""),
            payload=st.payload,
            timeout_ms=timeout_ms,
        )
        pk = self._rt.extensions["catalog_index"].provider_key(cand.provider_id)
        # case 5 also applies to the buffered path: a client cancel must not wait for the
        # (sync-only) provider to finish — race the generate against the cancel event.
        gen_task = asyncio.ensure_future(adapter.generate(req))
        cancel_task = asyncio.ensure_future(st.cancel.wait())
        try:
            done, _ = await asyncio.wait(
                {gen_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            cancel_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await cancel_task
        if gen_task not in done:
            gen_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await gen_task
            yield ev(
                StreamEventType.CANCELLED,
                {
                    "provider_key": pk,
                    "delivered_until_seq": st.summary.delivered_seq,
                    "partial_text_length": 0,
                    "synthesized": True,
                    "final": True,
                },
            )
            return
        resp = gen_task.result()
        if not resp.succeeded:
            err = resp.error
            yield ev(
                StreamEventType.FAILED,
                {
                    "reason": StreamFailureReason.PROVIDER_ERROR.value,
                    "resumable": False,
                    "synthesized": True,
                    "provider_key": pk,
                    "final": is_last,
                    "detail": f"{err.category.value}: {err.safe_message}" if err else None,
                },
            )
            return
        content = resp.output.get("content")
        if not isinstance(content, str):
            import json

            content = json.dumps(resp.output, sort_keys=True)
        st.summary = st.summary.model_copy(
            update={"text": content, "synthesized": True, "usage": dict(resp.usage)}
        )
        yield ev(StreamEventType.MESSAGE_DELTA, {"delta": content, "synthesized": True})
        if resp.usage:
            yield ev(StreamEventType.USAGE, dict(resp.usage))
        yield ev(
            StreamEventType.COMPLETED,
            {
                "provider_key": pk,
                "synthesized": True,
                "usage": dict(resp.usage),
                "text_length": len(content),
            },
        )

    def summary(self, stream_id: str) -> StreamSummary | None:
        st = self._streams.get(stream_id)
        return st.summary if st else None

    def prune(self, keep: int = 1000) -> None:
        if len(self._streams) > keep:
            for k in list(self._streams)[: len(self._streams) - keep]:
                self._streams.pop(k, None)

    @staticmethod
    def now() -> str:
        return utc_now().isoformat()
