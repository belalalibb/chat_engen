"""ChatService (CE-09): conversation-backed chat on top of HandoffPacket + the
ONE Router/ExecutionService. Also the single place CE-12 strategies and CE-19
skills hook into — every turn passes through ``build_packet``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from core.contracts.base import JsonObject
from core.contracts.provider import ProviderOperation
from core.contracts.routing import RoutingDecision, RoutingRequest
from core.routing.errors import FallbackNotConfigured, NoEligibleCandidates
from core.routing.router import UnsupportedPolicyType

from chat_engine.catalog import AliasUnconfirmed, CatalogService
from chat_engine.conversations.contracts import (
    ChatRequest,
    ChatResponse,
    Conversation,
    ConversationCreate,
    ConversationUpdate,
    Project,
    ProjectCreate,
    ProjectUpdate,
    Turn,
    TurnKind,
    TurnStatus,
)
from chat_engine.conversations.store import ConversationStorePort, NotFound
from chat_engine.conversations.summarizer import Summarizer
from chat_engine.handoff import (
    CanonicalMessage,
    CanonicalRole,
    HandoffBudget,
    HandoffCompiler,
    HandoffPacket,
    TextPart,
)
from chat_engine.util.time import utc_now

if TYPE_CHECKING:
    from chat_engine.composition.runtime import Runtime

DEFAULT_CONTEXT_WINDOW = 32_000
SUMMARIZE_WHEN_DROPPED = 6  # if the compiler would drop ≥ N turns, summarize instead


class ChatError(Exception):
    def __init__(
        self, code: str, message: str, *, http_status: int = 422, details: JsonObject | None = None
    ) -> None:
        self.code, self.message, self.http_status, self.details = (
            code,
            message,
            http_status,
            details or {},
        )
        super().__init__(message)


@dataclass
class Prepared:
    conversation: Conversation
    project: Project | None
    user_turn: Turn
    packet: HandoffPacket
    payload: JsonObject
    decision: RoutingDecision
    compiled: JsonObject
    skills_used: list[JsonObject]


class ChatService:
    def __init__(
        self, rt: Runtime, store: ConversationStorePort, *, summarizer: Summarizer | None = None
    ) -> None:
        self._rt = rt
        self._store = store
        self._summarizer = summarizer or Summarizer(rt)

    @property
    def store(self) -> ConversationStorePort:
        return self._store

    # -- projects ------------------------------------------------------------------------

    def create_project(self, req: ProjectCreate) -> Project:
        now = utc_now()
        return self._store.put_project(
            Project(
                id=uuid4(),
                tenant_id=self._rt.tenant_id,
                created_at=now,
                updated_at=now,
                **req.model_dump(),
            )
        )

    def update_project(self, project_id: UUID, req: ProjectUpdate) -> Project:
        p = self._store.get_project(self._rt.tenant_id, project_id)
        return self._store.put_project(
            p.model_copy(update={**req.model_dump(exclude_none=True), "updated_at": utc_now()})
        )

    def get_project(self, project_id: UUID) -> Project:
        return self._store.get_project(self._rt.tenant_id, project_id)

    def list_projects(self) -> list[Project]:
        return self._store.list_projects(self._rt.tenant_id)

    def delete_project(self, project_id: UUID) -> None:
        self._store.delete_project(self._rt.tenant_id, project_id)

    # -- conversations -------------------------------------------------------------------

    def create_conversation(self, req: ConversationCreate) -> Conversation:
        if req.project_id is not None:
            self._store.get_project(self._rt.tenant_id, req.project_id)
        now = utc_now()
        return self._store.put_conversation(
            Conversation(
                id=uuid4(),
                tenant_id=self._rt.tenant_id,
                user_id=self._rt.user_id,
                title=req.title or "New conversation",
                created_at=now,
                updated_at=now,
                **req.model_dump(exclude={"title"}),
            )
        )

    def update_conversation(self, conversation_id: UUID, req: ConversationUpdate) -> Conversation:
        c = self._store.get_conversation(self._rt.tenant_id, conversation_id)
        return self._store.put_conversation(
            c.model_copy(update={**req.model_dump(exclude_none=True), "updated_at": utc_now()})
        )

    def get_conversation(self, conversation_id: UUID) -> Conversation:
        return self._store.get_conversation(self._rt.tenant_id, conversation_id)

    def list_conversations(
        self, *, project_id: UUID | None = None, include_archived: bool = False
    ) -> list[Conversation]:
        return self._store.list_conversations(
            self._rt.tenant_id, project_id=project_id, include_archived=include_archived
        )

    def delete_conversation(self, conversation_id: UUID) -> None:
        self._store.delete_conversation(self._rt.tenant_id, conversation_id)

    def turns(
        self, conversation_id: UUID, *, after_seq: int | None = None, limit: int | None = None
    ) -> list[Turn]:
        self._store.get_conversation(self._rt.tenant_id, conversation_id)
        return self._store.turns(conversation_id, after_seq=after_seq, limit=limit)

    # -- the turn pipeline ---------------------------------------------------------------

    def _effective_skills(
        self, conversation: Conversation, project: Project | None, explicit: list[str] | None
    ) -> list[JsonObject]:
        """CE-19: explicit selection wins; else conversation overrides project assignment.
        Only ACTIVE/selectable skills (upstream SkillRegistry rule) are admitted; each
        record carries the version actually used."""
        assigner = self._rt.extensions.get("skills")
        if assigner is None:
            return []
        resolved = assigner.resolve_for_turn(
            conversation=conversation, project=project, explicit=explicit
        )
        return [dict(r) for r in resolved]

    def build_packet(
        self,
        conversation: Conversation,
        project: Project | None,
        *,
        message: str,
        skills_used: list[JsonObject],
        context_window: int | None,
    ) -> tuple[HandoffPacket, JsonObject]:
        """Turns → HandoffPacket. Summarizes older history when the compiler would drop too much."""
        compiler: HandoffCompiler = self._rt.extensions["handoff_compiler"]
        history = self._store.turns(conversation.id)
        summary_text = None
        if conversation.summary_turn_id is not None:
            s = next((t for t in history if t.id == conversation.summary_turn_id), None)
            if s is not None:
                summary_text = s.text()
                history = [t for t in history if t.seq > s.seq]
        msgs: list[CanonicalMessage] = []
        if project is not None and project.system_instructions:
            msgs.append(
                CanonicalMessage.of(
                    CanonicalRole.SYSTEM,
                    project.system_instructions,
                    source=f"project:{project.id}",
                )
            )
        for t in history:
            if t.kind in (TurnKind.MESSAGE, TurnKind.TOOL) and t.status is not TurnStatus.FAILED:
                msgs.append(
                    CanonicalMessage(
                        role=t.role,
                        parts=list(t.parts),
                        source=f"turn:{t.id}",
                        tool_call_id=t.metadata.get("tool_call_id")
                        if isinstance(t.metadata.get("tool_call_id"), str)
                        else None,
                    )
                )
        msgs.append(CanonicalMessage.of(CanonicalRole.USER, message, source="request"))
        constraints = list(conversation.constraints) + (
            list(project.default_constraints) if project else []
        )
        packet = HandoffPacket(
            packet_id=str(uuid4()),
            objective=conversation.objective or "",
            constraints=constraints,
            messages=msgs,
            summary=summary_text,
            budget=HandoffBudget(
                context_window=context_window
                or (project.context_window_hint if project else None)
                or DEFAULT_CONTEXT_WINDOW
            ),
            skills=skills_used,
        )
        compiled = compiler.compile(packet)
        meta: JsonObject = {
            "packet_id": packet.packet_id,
            "estimated_tokens": compiled.estimated_tokens,
            "dropped_messages": compiled.dropped_messages,
            "summarized": False,
        }
        return packet, {"messages": compiled.messages, **meta}

    async def maybe_summarize(self, conversation: Conversation, dropped: int) -> Conversation:
        """When truncation would drop ≥ N turns, fold older history into a structured summary turn."""
        if dropped < SUMMARIZE_WHEN_DROPPED:
            return conversation
        history = self._store.turns(conversation.id)
        keep_tail = 4
        if len(history) <= keep_tail + 2:
            return conversation
        older, _tail = history[:-keep_tail], history[-keep_tail:]
        prev_summary = None
        if conversation.summary_turn_id is not None:
            prev = next((t for t in older if t.id == conversation.summary_turn_id), None)
            prev_summary = prev.text() if prev else None
            older = [t for t in older if t.kind is not TurnKind.SUMMARY]
        text, provenance = await self._summarizer.summarize(
            older, previous_summary=prev_summary, objective=conversation.objective
        )
        summary_turn = Turn(
            id=uuid4(),
            conversation_id=conversation.id,
            seq=self._store.next_seq(conversation.id),
            kind=TurnKind.SUMMARY,
            role=CanonicalRole.SYSTEM,
            parts=[TextPart(text=text)],
            created_at=utc_now(),
            metadata={
                "covers_turns": [str(t.id) for t in older],
                "covers_seq_until": older[-1].seq,
                **provenance,
            },
        )
        self._store.append_turn(summary_turn)
        # the summary REPLACES older turns in future packets: pin it as the cut point
        return self._store.put_conversation(
            conversation.model_copy(
                update={
                    "summary_turn_id": summary_turn.id,
                    "updated_at": utc_now(),
                    "turn_count": conversation.turn_count + 1,
                }
            )
        )

    def route(
        self, model: str | None, provider: str | None, required_capabilities: list[str]
    ) -> RoutingDecision:
        catalog: CatalogService = self._rt.extensions["catalog"]
        policy = None
        if model:
            try:
                policy = catalog.resolve_policy(model, provider_key=provider)
            except AliasUnconfirmed:
                raise ChatError(
                    "alias_unconfirmed",
                    f"'{model}' has only proposed aliases; confirm one first.",
                    details={"model": model},
                ) from None
        try:
            return self._rt.router.route(
                RoutingRequest(
                    operation=ProviderOperation.GENERATE_TEXT,
                    model_policy=policy,
                    required_capabilities=list(required_capabilities),
                )
            )
        except UnsupportedPolicyType as exc:
            raise ChatError("conflict", str(exc)) from None
        except NoEligibleCandidates as exc:
            raise ChatError(
                "not_found",
                "No eligible model candidates for this request.",
                http_status=503,
                details={
                    "excluded": [r.model_dump(mode="json", exclude_none=True) for r in exc.excluded]
                },
            ) from None
        except FallbackNotConfigured as exc:
            raise ChatError("conflict", str(exc), http_status=503) from None

    async def prepare(self, req: ChatRequest) -> Prepared:
        project: Project | None = None
        if req.conversation_id is not None:
            conversation = self._store.get_conversation(self._rt.tenant_id, req.conversation_id)
        else:
            conversation = self.create_conversation(
                ConversationCreate(
                    title=req.message[:60], project_id=req.project_id, model=req.model
                )
            )
        if conversation.project_id is not None:
            project = self._store.get_project(self._rt.tenant_id, conversation.project_id)
        model = req.model or conversation.model or (project.default_model if project else None)
        skills_used = self._effective_skills(conversation, project, req.skills)
        decision = self.route(model, req.provider, req.required_capabilities)
        model_row = self._rt.models.get_by_id(decision.selected.model_id)
        # capability gate (CE-06) for explicit requirements
        ledger = self._rt.extensions.get("capabilities")
        if ledger is not None and req.required_capabilities:
            pk = (
                self._rt.extensions["catalog_index"].provider_key(decision.selected.provider_id)
                or ""
            )
            for cap in req.required_capabilities:
                verdict = ledger.gate(pk, cap)
                if not verdict.allowed:
                    raise ChatError(
                        "unsupported_capability",
                        f"capability '{cap}' is not executable on '{pk}': {verdict.reason}",
                        details={"capability": cap, "provider_key": pk},
                    )

        user_turn = Turn(
            id=uuid4(),
            conversation_id=conversation.id,
            seq=self._store.next_seq(conversation.id),
            role=CanonicalRole.USER,
            parts=[TextPart(text=req.message)],
            created_at=utc_now(),
            skills_used=skills_used,
            metadata={"attachments": req.attachments} if req.attachments else {},
        )
        packet, compiled = self.build_packet(
            conversation,
            project,
            message=req.message,
            skills_used=skills_used,
            context_window=model_row.context_window,
        )
        if int(compiled["dropped_messages"]) >= SUMMARIZE_WHEN_DROPPED:
            conversation = await self.maybe_summarize(
                conversation, int(compiled["dropped_messages"])
            )
            packet, compiled = self.build_packet(
                conversation,
                project,
                message=req.message,
                skills_used=skills_used,
                context_window=model_row.context_window,
            )
            compiled["summarized"] = True
        payload: JsonObject = {
            "ask": req.message,
            "messages": compiled["messages"],
            "handoff_packet_id": packet.packet_id,
        }
        if req.generation:
            payload["generation"] = dict(req.generation)
        if req.tools:
            payload["tools"] = list(req.tools)
            payload["tool_choice"] = "auto"
        if skills_used:
            payload["skills"] = skills_used
        return Prepared(
            conversation=conversation,
            project=project,
            user_turn=user_turn,
            packet=packet,
            payload=payload,
            decision=decision,
            compiled=compiled,
            skills_used=skills_used,
        )

    async def chat(self, req: ChatRequest) -> ChatResponse:
        prep = await self.prepare(req)
        self._store.append_turn(prep.user_turn)
        if req.strategy and req.strategy != "single":
            return await self._chat_orchestrated(req, prep)
        request_hash = hashlib.sha256(
            json.dumps(req.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()
        admission = self._rt.extensions.get("admission")
        reservation = None
        pk = (
            self._rt.extensions["catalog_index"].provider_key(prep.decision.selected.provider_id)
            or "?"
        )
        if admission is not None:
            from chat_engine.admission import Denied

            got = await admission.reserve(
                [f"tenant:{self._rt.tenant_id}", f"provider:{pk}"],
                tokens_estimate=int(prep.compiled["estimated_tokens"]),
                units=1.0,
            )
            if isinstance(got, Denied):
                raise ChatError(
                    "admission_denied",
                    "Admission denied for this request.",
                    http_status=429,
                    details={
                        "scope": got.scope,
                        "dimension": got.dimension.value,
                        "retry_after_ms": got.retry_after_ms,
                    },
                )
            reservation = got
        try:
            report = await self._rt.execution.execute_single(
                tenant_id=self._rt.tenant_id,
                user_id=self._rt.user_id,
                decision=prep.decision,
                operation=ProviderOperation.GENERATE_TEXT,
                payload=prep.payload,
                request_hash=request_hash,
                conversation_id=prep.conversation.id,
            )
        except Exception:
            if admission is not None and reservation is not None:
                await admission.release(reservation)
            raise
        node = report.nodes[-1] if report.nodes else None
        resp = node.response if node else None
        usage = dict(resp.usage) if resp else {}
        if admission is not None and reservation is not None:
            total = usage.get("total_tokens")
            await admission.settle(
                reservation,
                tokens_actual=total if isinstance(total, int) else None,
                success=report.execution.status.value == "succeeded",
            )
        self._rt.executions.put(report)
        provider_key = pk
        model_key = str(self._rt.models.get_by_id(prep.decision.selected.model_id).model_key)
        winning = next((a for a in reversed(node.attempts) if a.succeeded), None) if node else None
        if winning is not None:  # router fallback may have answered from another provider
            provider_key = (
                self._rt.extensions["catalog_index"].provider_key(winning.candidate.provider_id)
                or pk
            )
            model_key = str(self._rt.models.get_by_id(winning.candidate.model_id).model_key)
        catalog: CatalogService = self._rt.extensions["catalog"]
        canonical = None
        if model_key:
            ids = [
                a.canonical_id
                for a in catalog.list_aliases(alias=model_key)
                if a.source.value == "system"
            ]
            canonical = ids[0] if ids else model_key
        if report.execution.status.value != "succeeded" or resp is None or not resp.succeeded:
            err = resp.error if resp else None
            failed = Turn(
                id=uuid4(),
                conversation_id=prep.conversation.id,
                seq=self._store.next_seq(prep.conversation.id),
                role=CanonicalRole.ASSISTANT,
                parts=[],
                status=TurnStatus.FAILED,
                provider_key=provider_key,
                model_key=model_key,
                execution_id=str(report.execution.id),
                handoff_packet_id=prep.packet.packet_id,
                created_at=utc_now(),
                metadata={
                    "error": {"category": err.category.value, "message": err.safe_message}
                    if err
                    else {"message": "execution failed"}
                },
            )
            self._store.append_turn(failed)
            raise ChatError(
                "execution_failed",
                err.safe_message if err else "Execution failed.",
                http_status=502,
                details={
                    "execution_id": str(report.execution.id),
                    "turn_id": str(failed.id),
                    "provider_error_category": err.category.value if err else None,
                },
            )
        content = resp.output.get("content")
        if not isinstance(content, str):
            content = json.dumps(resp.output, sort_keys=True)
        parts: list[Any] = [TextPart(text=content)]
        tool_calls = resp.output.get("tool_calls")
        if isinstance(tool_calls, list):
            from chat_engine.handoff.contracts import ToolCallPart

            for tc in tool_calls:
                if isinstance(tc, dict):
                    fn = tc.get("function") or {}
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except ValueError:
                        args = {"_raw": fn.get("arguments")}
                    parts.append(
                        ToolCallPart(
                            tool_call_id=str(tc.get("id") or uuid4()),
                            name=str(fn.get("name") or "tool"),
                            arguments=args if isinstance(args, dict) else {"_raw": args},
                        )
                    )
        turn = Turn(
            id=uuid4(),
            conversation_id=prep.conversation.id,
            seq=self._store.next_seq(prep.conversation.id),
            role=CanonicalRole.ASSISTANT,
            parts=parts,
            provider_key=provider_key,
            model_key=model_key,
            canonical_id=canonical,
            usage=usage,
            latency_ms=resp.latency_ms,
            handoff_packet_id=prep.packet.packet_id,
            execution_id=str(report.execution.id),
            skills_used=prep.skills_used,
            created_at=utc_now(),
        )
        self._store.append_turn(turn)
        self._store.put_conversation(
            prep.conversation.model_copy(
                update={
                    "turn_count": prep.conversation.turn_count + 2,
                    "updated_at": utc_now(),
                    "title": prep.conversation.title
                    if prep.conversation.turn_count
                    else req.message[:60],
                }
            )
        )
        recorder = self._rt.extensions.get("usage_recorder")
        if recorder is not None:
            recorder.record_turn(
                turn,
                conversation=prep.conversation,
                provider_key=provider_key,
                model_key=model_key,
                canonical_id=canonical,
                status="succeeded",
            )
        return ChatResponse(
            conversation_id=prep.conversation.id,
            turn=turn,
            user_turn_id=prep.user_turn.id,
            execution_id=str(report.execution.id),
            provider_key=provider_key,
            model_key=model_key,
            usage=usage,
            handoff={k: v for k, v in prep.compiled.items() if k != "messages"},
        )

    async def _chat_orchestrated(self, req: ChatRequest, prep: Prepared) -> ChatResponse:
        """CE-12: multi-model strategies; the chosen step becomes the assistant turn and the
        full trace is persisted on the turn (``metadata.trace``) and returned."""
        from chat_engine.orchestration import Strategy, StrategyOptions

        orch = self._rt.extensions.get("orchestrator")
        if orch is None:
            raise ChatError("conflict", "orchestration is not enabled", http_status=503)
        try:
            strategy = Strategy(req.strategy or "single")
        except ValueError:
            raise ChatError(
                "conflict",
                f"unknown strategy '{req.strategy}'",
                http_status=422,
                details={"allowed": [s.value for s in Strategy]},
            ) from None
        opts = StrategyOptions.model_validate(req.strategy_options)
        if opts.provider is None and req.provider:
            opts = opts.model_copy(update={"provider": req.provider})
        default_model = (
            req.model
            or prep.conversation.model
            or (prep.project.default_model if prep.project else None)
        )
        messages = [dict(m) for m in prep.payload["messages"]]
        try:
            result, trace = await orch.run(
                strategy,
                opts,
                messages=messages,
                ask=req.message,
                packet_id=prep.packet.packet_id,
                conversation_id=prep.conversation.id,
                generation=dict(req.generation) if req.generation else None,
                default_model=default_model,
            )
        except ChatError as exc:
            failed = Turn(
                id=uuid4(),
                conversation_id=prep.conversation.id,
                seq=self._store.next_seq(prep.conversation.id),
                role=CanonicalRole.ASSISTANT,
                parts=[],
                status=TurnStatus.FAILED,
                handoff_packet_id=prep.packet.packet_id,
                created_at=utc_now(),
                metadata={
                    "error": {"code": exc.code, "message": exc.message},
                    "strategy": strategy.value,
                },
            )
            self._store.append_turn(failed)
            exc.details = {**exc.details, "turn_id": str(failed.id)}
            raise
        trace_json = trace.model_dump(mode="json", exclude_none=True)
        turn = Turn(
            id=uuid4(),
            conversation_id=prep.conversation.id,
            seq=self._store.next_seq(prep.conversation.id),
            role=CanonicalRole.ASSISTANT,
            parts=[TextPart(text=result.text)],
            provider_key=result.provider_key,
            model_key=result.model_key,
            canonical_id=result.canonical_id,
            usage=dict(result.usage),
            latency_ms=result.latency_ms,
            handoff_packet_id=prep.packet.packet_id,
            execution_id=result.execution_id,
            skills_used=prep.skills_used,
            created_at=utc_now(),
            metadata={"strategy": strategy.value, "trace": trace_json},
        )
        self._store.append_turn(turn)
        self._store.put_conversation(
            prep.conversation.model_copy(
                update={
                    "turn_count": prep.conversation.turn_count + 2,
                    "updated_at": utc_now(),
                    "title": prep.conversation.title
                    if prep.conversation.turn_count
                    else req.message[:60],
                }
            )
        )
        recorder = self._rt.extensions.get("usage_recorder")
        if recorder is not None:
            recorder.record_turn(
                turn,
                conversation=prep.conversation,
                provider_key=result.provider_key,
                model_key=result.model_key,
                canonical_id=result.canonical_id,
                status="succeeded",
            )
        return ChatResponse(
            conversation_id=prep.conversation.id,
            turn=turn,
            user_turn_id=prep.user_turn.id,
            execution_id=result.execution_id,
            provider_key=result.provider_key,
            model_key=result.model_key,
            usage=dict(result.usage),
            handoff={k: v for k, v in prep.compiled.items() if k != "messages"},
            trace=trace_json,
        )

    def record_stream_turn(
        self,
        conversation_id: UUID,
        *,
        text: str,
        status: TurnStatus,
        provider_key: str | None,
        model_key: str | None,
        usage: JsonObject,
        stream_id: str,
        packet_id: str | None,
        skills_used: list[JsonObject],
    ) -> Turn:
        conv = self._store.get_conversation(self._rt.tenant_id, conversation_id)
        turn = Turn(
            id=uuid4(),
            conversation_id=conversation_id,
            seq=self._store.next_seq(conversation_id),
            role=CanonicalRole.ASSISTANT,
            parts=[TextPart(text=text)] if text else [],
            status=status,
            provider_key=provider_key,
            model_key=model_key,
            usage=dict(usage),
            stream_id=stream_id,
            handoff_packet_id=packet_id,
            skills_used=skills_used,
            created_at=utc_now(),
        )
        self._store.append_turn(turn)
        self._store.put_conversation(
            conv.model_copy(update={"turn_count": conv.turn_count + 1, "updated_at": utc_now()})
        )
        return turn


__all__ = ["ChatError", "ChatService", "NotFound", "Prepared"]
