"""``POST /v1/execute`` — upstream-compatible surface served by chat_engine.

Same ``ExecuteRequest`` contract, same response/error shapes (golden-tested
against snapshots captured from upstream). Scope mirrors upstream's single-
process slice: ``single`` strategy via the ONE Router + ExecutionService,
``explicit_models`` via MultiModelExecutor, ``async`` via an in-process task.
``stream=true`` stays 422 here (contract) with an additive ``hint`` towards
``/v1/chat/stream`` (CE-08).
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
from typing import Annotated, Any
from uuid import UUID, uuid4

from core.context.errors import ContextBudgetExceeded
from core.contracts.base import JsonObject
from core.contracts.context import ContextComposeRequest
from core.contracts.conversation import Conversation, ConversationStatus, Message, MessageRole
from core.contracts.errors import ErrorCode
from core.contracts.execute import (
    ExecuteAsyncAccepted,
    ExecuteRequest,
    ExecuteSyncResponse,
    ExecutionContextInfo,
    ExecutionProgress,
    ExecutionResult,
    ExecutionStatus,
    ExecutionStatusResponse,
    UsageReport,
)
from core.contracts.execution import ExecutionNodeStatus, ExecutionStrategy
from core.contracts.model_policy import AgentNodeMappingPolicy, ExplicitModelsPolicy
from core.contracts.provider import ProviderError, ProviderOperation
from core.contracts.routing import RoutingRequest
from core.contracts.usage import UsageLedger
from core.execution.multi_model import CompareRefused, InvalidJudgePolicy, UnsupportedStrategy
from core.execution.service import ExecutionReport
from core.memory.errors import ConversationNotFound
from core.routing.errors import FallbackNotConfigured, NoEligibleCandidates
from core.routing.router import UnsupportedPolicyType
from core.usage.errors import BudgetExceeded, EntitlementNotConfigured
from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse

from chat_engine.api.errors import error_response, execution_failure_detail
from chat_engine.composition.runtime import Runtime
from chat_engine.store.executions import ExecutionNotFound
from chat_engine.util.time import utc_now

REQUEST_CONTEXT_KEY = "request_context"


class AdmissionDenied(Exception):
    def __init__(self, denied: Any) -> None:
        self.denied = denied
        super().__init__(denied.reason)


_RUNNABLE_STRATEGIES = frozenset({"single"})


def request_hash(body: ExecuteRequest) -> str:
    canonical = json.dumps(
        body.model_dump(mode="json", by_alias=True, exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def result_from_output(
    output: JsonObject, format_hint: str | None, artifacts: list[JsonObject] | None = None
) -> ExecutionResult:
    content = output.get("content")
    if not isinstance(content, str):
        content = json.dumps(output, sort_keys=True)
    return ExecutionResult(
        type="message", content=content, format=format_hint, artifacts=list(artifacts or [])
    )


def usage_report(ledger: UsageLedger | None) -> UsageReport | None:
    if ledger is None:
        return None
    return UsageReport(
        units_reserved=int(ledger.units_reserved),
        units_settled=int(ledger.units_settled),
        details={"status": ledger.status.value},
    )


def last_provider_error(report: ExecutionReport) -> ProviderError | None:
    for node_report in reversed(report.nodes):
        for attempt in reversed(node_report.attempts):
            if attempt.error is not None:
                return attempt.error
    return None


def failed_stage(report: ExecutionReport) -> str | None:
    for node_report in report.nodes:
        node = node_report.node
        if node.status is ExecutionNodeStatus.FAILED and node.error:
            stage = node.error.get("stage")
            if isinstance(stage, str):
                return stage
    return None


def context_provenance(report: ExecutionReport) -> JsonObject | None:
    if not report.nodes:
        return None
    input_ref = report.nodes[-1].node.input_ref
    if not isinstance(input_ref, dict):
        return None
    context = input_ref.get("context")
    if not isinstance(context, dict):
        return None
    blocks = context.get("context_blocks")
    if not isinstance(blocks, list):
        return None
    blocks = [b for b in blocks if isinstance(b, dict)]
    memory_blocks: list[JsonObject] = []
    for block in blocks:
        source = block.get("source")
        if isinstance(source, str) and source.startswith("memory:"):
            memory_blocks.append(
                {
                    "memory_id": source.removeprefix("memory:"),
                    "source": None,
                    "type": block.get("type"),
                }
            )
    excluded = context.get("excluded")
    return {
        "type": "context_provenance",
        "blocks_total": len(blocks),
        "memory_blocks": memory_blocks,
        "gold_blocks": 0,
        "excluded": len(excluded) if isinstance(excluded, list) else 0,
    }


def with_request_context(report: ExecutionReport, context: JsonObject) -> ExecutionReport:
    execution = report.execution
    snapshot = dict(execution.cost_snapshot)
    snapshot[REQUEST_CONTEXT_KEY] = dict(context)
    return dataclasses.replace(
        report, execution=execution.model_copy(update={"cost_snapshot": snapshot})
    )


def context_info(report: ExecutionReport) -> ExecutionContextInfo:
    stored = report.execution.cost_snapshot.get(REQUEST_CONTEXT_KEY)
    stored = stored if isinstance(stored, dict) else {}
    mode = stored.get("mode")
    project_id = stored.get("project_id")
    template_ref = stored.get("template_ref")
    return ExecutionContextInfo(
        strategy=report.execution.strategy.value,
        mode=str(mode) if isinstance(mode, str) else None,
        project_id=str(project_id) if isinstance(project_id, str) else None,
        template_ref=str(template_ref) if isinstance(template_ref, str) else None,
    )


def progress(report: ExecutionReport) -> ExecutionProgress:
    terminal = ("succeeded", "failed", "skipped")
    total = len(report.nodes)
    done = sum(1 for e in report.nodes if e.node.status.value in terminal)
    percent = round(100 * done / total) if total else None
    current = report.nodes[-1].node.node_key if report.nodes else None
    return ExecutionProgress(current_stage=current, percent=percent)


def async_accepted(execution_id: UUID) -> JSONResponse:
    accepted = ExecuteAsyncAccepted(
        execution_id=str(execution_id),
        status=ExecutionStatus.QUEUED,
        poll_url=f"/v1/executions/{execution_id}",
    )
    return JSONResponse(
        status_code=202, content=accepted.model_dump(mode="json", exclude_none=True)
    )


def sync_response(report: ExecutionReport, body: ExecuteRequest) -> JSONResponse:
    if report.execution.status is ExecutionStatus.SUCCEEDED:
        output = report.final_output
        assert output is not None
        format_hint = body.output.format if body.output is not None else None
        provenance = context_provenance(report)
        sync = ExecuteSyncResponse(
            execution_id=str(report.execution.id),
            status=report.execution.status,
            result=result_from_output(output, format_hint, [provenance] if provenance else None),
            usage=usage_report(report.usage),
            evaluation=None,
            context=context_info(report),
        )
        return JSONResponse(
            status_code=200, content=sync.model_dump(mode="json", exclude_none=True)
        )
    detail = execution_failure_detail(
        str(report.execution.id), last_provider_error(report), stage=failed_stage(report)
    )
    from chat_engine.api.errors import HTTP_STATUS_BY_CODE

    return JSONResponse(
        status_code=HTTP_STATUS_BY_CODE[detail.code],
        content={"error": detail.model_dump(mode="json", exclude_none=True)},
    )


def status_response(report: ExecutionReport) -> JSONResponse:
    result = None
    if report.execution.status is ExecutionStatus.SUCCEEDED and report.final_output is not None:
        provenance = context_provenance(report)
        result = result_from_output(report.final_output, None, [provenance] if provenance else None)
    body = ExecutionStatusResponse(
        execution_id=str(report.execution.id),
        status=report.execution.status,
        progress=progress(report),
        result=result,
        context=context_info(report),
    )
    return JSONResponse(status_code=200, content=body.model_dump(mode="json", exclude_none=True))


# --- the handler ---------------------------------------------------------------------


@dataclasses.dataclass
class Admitted:
    """Everything decided before execution — reused by /v1/chat (CE-09)."""

    payload: JsonObject
    run_context: JsonObject
    conversation_id: UUID | None
    policy: Any
    multi_model_policy: ExplicitModelsPolicy | None


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    def _request_context(body: ExecuteRequest) -> JsonObject:
        policy = body.execution_policy
        ctx: JsonObject = {
            "mode": "single",
            "async": bool(policy is not None and policy.async_ is True),
        }
        if body.project_id is not None:
            ctx["project_id"] = body.project_id
        return ctx

    def _admission_scopes(decision: Any) -> list[str]:
        scopes = [f"tenant:{rt.tenant_id}"]
        if decision is not None:
            pk = rt.extensions["catalog_index"].provider_key(decision.selected.provider_id)
            if pk:
                scopes.append(f"provider:{pk}")
        return scopes

    async def _run(
        body: ExecuteRequest,
        *,
        payload: JsonObject,
        idempotency_key: str | None,
        conversation_id: UUID | None,
        multi_model_policy: ExplicitModelsPolicy | None,
        decision: Any,
    ) -> ExecutionReport:
        admission = rt.extensions.get("admission")
        reservation = None
        if admission is not None:
            from chat_engine.admission import Denied

            est = (
                sum(
                    len(str(m.get("content", "")))
                    for m in payload.get("messages", [])
                    if isinstance(m, dict)
                )
                // 4
            )
            got = await admission.reserve(
                _admission_scopes(decision), tokens_estimate=est, units=1.0
            )
            if isinstance(got, Denied):
                raise AdmissionDenied(got)
            reservation = got
        try:
            report = await _run_inner(
                body,
                payload=payload,
                idempotency_key=idempotency_key,
                conversation_id=conversation_id,
                multi_model_policy=multi_model_policy,
                decision=decision,
            )
        except Exception:
            if admission is not None and reservation is not None:
                await admission.release(reservation)
            raise
        if admission is not None and reservation is not None:
            usage = (
                report.nodes[-1].response.usage
                if report.nodes and report.nodes[-1].response
                else {}
            )
            total = usage.get("total_tokens") if isinstance(usage, dict) else None
            await admission.settle(
                reservation,
                tokens_actual=total if isinstance(total, int) else None,
                success=report.execution.status is ExecutionStatus.SUCCEEDED,
            )
        return report

    async def _run_inner(
        body: ExecuteRequest,
        *,
        payload: JsonObject,
        idempotency_key: str | None,
        conversation_id: UUID | None,
        multi_model_policy: ExplicitModelsPolicy | None,
        decision: Any,
    ) -> ExecutionReport:
        if multi_model_policy is not None:
            multi = await rt.multi_model.execute(
                tenant_id=rt.tenant_id,
                user_id=rt.user_id,
                policy=multi_model_policy,
                operation=ProviderOperation.GENERATE_TEXT,
                payload=payload,
                request_hash=request_hash(body),
                idempotency_key=idempotency_key,
                conversation_id=conversation_id,
            )
            return multi.final_report
        assert decision is not None
        return await rt.execution.execute_single(
            tenant_id=rt.tenant_id,
            user_id=rt.user_id,
            decision=decision,
            operation=ProviderOperation.GENERATE_TEXT,
            payload=payload,
            request_hash=request_hash(body),
            idempotency_key=idempotency_key,
            conversation_id=conversation_id,
        )

    @router.post("/v1/execute")
    async def execute(
        request: Request,
        body: ExecuteRequest,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> Response:
        policy = body.execution_policy
        if policy is not None and policy.stream is True:
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "Streaming is not available on this deployment slice.",
                details={"field": "execution_policy.stream"},
            )
        if (
            policy is not None
            and policy.strategy is not None
            and policy.strategy not in _RUNNABLE_STRATEGIES
        ):
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "execution_policy.strategy is not runnable on this deployment "
                f"slice: {policy.strategy!r} (supported: single)",
                details={"field": "execution_policy.strategy"},
            )
        if body.execution_strategy is not None:
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "execution_strategy is not available on this deployment slice.",
                details={"field": "execution_strategy"},
            )
        if isinstance(body.model_policy, AgentNodeMappingPolicy):
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "model_policy.type=agent_node_mapping is not available on this deployment slice.",
                details={"field": "model_policy"},
            )
        conversation_id: UUID | None = None
        if body.conversation_id is not None:
            try:
                conversation_id = UUID(body.conversation_id)
            except ValueError:
                return error_response(
                    ErrorCode.VALIDATION_ERROR,
                    "conversation_id must be a UUID.",
                    details={"field": "conversation_id"},
                )
        if body.role is not None:
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "role is not selectable on this deployment slice.",
                details={"field": "role"},
            )
        admitted_skills: list[JsonObject] = []
        if body.skills:
            selectable = {s.manifest.id: s for s in rt.skills.list_selectable()}
            seen: set[str] = set()
            for requested in body.skills:
                if requested in seen:
                    return error_response(
                        ErrorCode.VALIDATION_ERROR,
                        f"skill requested twice: {requested}",
                        details={"field": "skills"},
                    )
                seen.add(requested)
                skill = selectable.get(requested)
                if skill is None:
                    return error_response(
                        ErrorCode.VALIDATION_ERROR,
                        f"skill is not selectable: {requested}",
                        details={"field": "skills"},
                    )
                admitted_skills.append(
                    {"id": skill.manifest.id, "name": skill.name, "version": skill.version}
                )

        run_context = _request_context(body)

        if idempotency_key is not None:
            replay_id = rt.idempotency.get((rt.tenant_id, idempotency_key))
            if replay_id is not None:
                replayed = rt.executions.get(rt.tenant_id, replay_id)
                if replayed.execution.request_hash != request_hash(body):
                    return error_response(
                        ErrorCode.VALIDATION_ERROR,
                        "Idempotency-Key was already used by this tenant with a different request body; "
                        "reuse the key only with the identical request, or send a new key.",
                        details={
                            "reason": "idempotency_conflict",
                            "field": "Idempotency-Key",
                            "execution_id": str(replayed.execution.id),
                        },
                        http_status=409,
                    )
                if replayed.execution.status in (ExecutionStatus.QUEUED, ExecutionStatus.RUNNING):
                    return async_accepted(replayed.execution.id)
                return sync_response(replayed, body)

        conversation: Conversation | None = None
        if conversation_id is not None:
            try:
                conversation = rt.conversations.get_conversation(rt.tenant_id, conversation_id)
            except ConversationNotFound:
                conversation = rt.conversations.create_conversation(
                    Conversation(
                        id=conversation_id,
                        tenant_id=rt.tenant_id,
                        user_id=rt.user_id,
                        title=body.ask[:80],
                        status=ConversationStatus.ACTIVE,
                    )
                )
            if conversation.user_id != rt.user_id:
                return error_response(
                    ErrorCode.UNAUTHORIZED,
                    "Conversation belongs to another user.",
                    details={"field": "conversation_id"},
                )

        try:
            composed = rt.composer.compose(
                ContextComposeRequest(
                    tenant_id=rt.tenant_id,
                    user_id=rt.user_id,
                    ask=body.ask,
                    conversation_id=conversation.id if conversation is not None else None,
                    context_budget=rt.context_budget,
                )
            )
        except ContextBudgetExceeded as exc:
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "Mandatory context exceeds the context budget.",
                details={"required": exc.required, "budget": exc.budget},
            )

        if conversation is not None:
            rt.conversations.append_message(
                rt.tenant_id,
                Message(
                    id=uuid4(),
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    content=body.ask,
                    created_at=utc_now(),
                ),
            )

        effective_policy = body.model_policy
        multi_model_policy = (
            effective_policy if isinstance(effective_policy, ExplicitModelsPolicy) else None
        )
        required_capabilities = (
            list(body.requirements.capabilities) if body.requirements is not None else []
        )
        required_modalities = (
            list(body.requirements.modalities) if body.requirements is not None else []
        )

        decision = None
        if multi_model_policy is None:
            try:
                decision = rt.router.route(
                    RoutingRequest(
                        operation=ProviderOperation.GENERATE_TEXT,
                        model_policy=effective_policy,
                        required_capabilities=required_capabilities,
                        required_modalities=required_modalities,
                    )
                )
            except UnsupportedPolicyType as exc:
                return error_response(
                    ErrorCode.VALIDATION_ERROR, str(exc), details={"field": "model_policy"}
                )
            except NoEligibleCandidates as exc:
                details: JsonObject = {
                    "excluded": [r.model_dump(mode="json", exclude_none=True) for r in exc.excluded]
                }
                if exc.retry_after_ms is not None:
                    details["retry_after_ms"] = exc.retry_after_ms
                return error_response(
                    ErrorCode.MODEL_UNAVAILABLE,
                    "No eligible model candidates for this request.",
                    details=details,
                )
            except FallbackNotConfigured as exc:
                return error_response(ErrorCode.MODEL_UNAVAILABLE, str(exc))

        ledger = rt.extensions.get("capabilities")
        if ledger is not None and decision is not None and required_capabilities:
            pk = rt.extensions["catalog_index"].provider_key(decision.selected.provider_id) or ""
            for cap in required_capabilities:
                verdict = ledger.gate(pk, cap)
                if not verdict.allowed:
                    return error_response(
                        ErrorCode.CAPABILITY_DENIED,
                        f"capability '{cap}' is not executable on provider '{pk}': {verdict.reason}",
                        details={
                            "capability": cap,
                            "provider_key": pk,
                            "reason": verdict.reason,
                            "ce_code": "unsupported_capability",
                        },
                    )

        payload: JsonObject = {"ask": body.ask}
        if body.output is not None:
            payload["output"] = body.output.model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
        payload["context"] = composed.model_dump(mode="json", exclude_none=True)
        if admitted_skills:
            payload["skills"] = admitted_skills
        # CE-03 additive: compile the composed context into OpenAI-style messages
        # alongside the legacy keys (old adapters ignore it; new ones prefer it).
        compiler = rt.extensions.get("handoff_compiler")
        if compiler is not None:
            payload["messages"] = compiler.messages_from_composed(composed)

        if policy is not None and policy.async_ is True:
            if multi_model_policy is not None:
                return error_response(
                    ErrorCode.VALIDATION_ERROR,
                    "explicit_models is not supported on the async path in this slice; run it synchronously.",
                    details={"field": "model_policy"},
                )
            execution_id = uuid4()
            from core.contracts.execution import Execution

            placeholder = ExecutionReport(
                execution=Execution(
                    id=execution_id,
                    tenant_id=rt.tenant_id,
                    user_id=rt.user_id,
                    conversation_id=conversation_id,
                    request_hash=request_hash(body),
                    idempotency_key=idempotency_key,
                    status=ExecutionStatus.QUEUED,
                    strategy=ExecutionStrategy.SINGLE,
                    cost_snapshot={REQUEST_CONTEXT_KEY: dict(run_context)},
                    created_at=utc_now(),
                ),
                nodes=(),
                status_history=(ExecutionStatus.QUEUED,),
            )
            rt.executions.put(placeholder)
            if idempotency_key is not None:
                rt.idempotency[(rt.tenant_id, idempotency_key)] = execution_id

            async def _background() -> None:
                try:
                    report = await _run(
                        body,
                        payload=payload,
                        idempotency_key=idempotency_key,
                        conversation_id=conversation_id,
                        multi_model_policy=None,
                        decision=decision,
                    )
                    report = dataclasses.replace(
                        report, execution=report.execution.model_copy(update={"id": execution_id})
                    )
                    rt.executions.put(with_request_context(report, run_context))
                except Exception:
                    failed = dataclasses.replace(
                        placeholder,
                        execution=placeholder.execution.model_copy(
                            update={"status": ExecutionStatus.FAILED, "completed_at": utc_now()}
                        ),
                        status_history=(ExecutionStatus.QUEUED, ExecutionStatus.FAILED),
                    )
                    rt.executions.put(failed)

            task = asyncio.create_task(_background())
            rt.background_tasks.add(task)
            task.add_done_callback(rt.background_tasks.discard)
            return async_accepted(execution_id)

        try:
            try:
                report = await _run(
                    body,
                    payload=payload,
                    idempotency_key=idempotency_key,
                    conversation_id=conversation_id,
                    multi_model_policy=multi_model_policy,
                    decision=decision,
                )
            except (UnsupportedStrategy, InvalidJudgePolicy) as exc:
                return error_response(
                    ErrorCode.VALIDATION_ERROR, str(exc), details={"field": "model_policy"}
                )
            except CompareRefused as exc:
                return error_response(ErrorCode.MODEL_UNAVAILABLE, str(exc))
            except AdmissionDenied as exc:
                d = exc.denied
                denied_details: JsonObject = {
                    "scope": d.scope,
                    "dimension": d.dimension.value,
                    "limit": d.limit,
                    "current": d.current,
                    "reason": d.reason,
                }
                if d.retry_after_ms is not None:
                    denied_details["retry_after_ms"] = d.retry_after_ms
                return error_response(
                    ErrorCode.RATE_LIMITED,
                    "Admission denied for this request.",
                    retryable=True,
                    details=denied_details,
                )
        except BudgetExceeded as exc:
            return error_response(
                ErrorCode.ENTITLEMENT_EXCEEDED,
                "Task-unit budget exceeded for this request.",
                details={"requested": exc.requested, "remaining": exc.remaining},
            )
        except EntitlementNotConfigured:
            return error_response(
                ErrorCode.ENTITLEMENT_EXCEEDED,
                "No task-unit entitlement is configured for this tenant.",
            )

        report = with_request_context(report, run_context)
        rt.executions.put(report)
        if idempotency_key is not None:
            rt.idempotency[(rt.tenant_id, idempotency_key)] = report.execution.id
        if (
            conversation is not None
            and report.execution.status is ExecutionStatus.SUCCEEDED
            and report.final_output is not None
        ):
            format_hint = body.output.format if body.output is not None else None
            rt.conversations.append_message(
                rt.tenant_id,
                Message(
                    id=uuid4(),
                    conversation_id=conversation.id,
                    role=MessageRole.ASSISTANT,
                    content=result_from_output(report.final_output, format_hint).content,
                    created_at=utc_now(),
                ),
            )
        return sync_response(report, body)

    @router.get("/v1/executions/{execution_id}")
    async def get_execution(execution_id: str) -> Response:
        try:
            report = rt.executions.get(rt.tenant_id, UUID(execution_id))
        except (ValueError, ExecutionNotFound):
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "Unknown execution.",
                details={"field": "execution_id"},
                http_status=404,
            )
        return status_response(report)

    @router.get("/v1/executions")
    async def list_executions(status: str | None = None, limit: int = 50) -> Response:
        status_filter = None
        if status is not None:
            try:
                status_filter = ExecutionStatus(status)
            except ValueError:
                return error_response(
                    ErrorCode.VALIDATION_ERROR,
                    "Unknown status filter.",
                    details={"field": "status"},
                )
        rows = rt.executions.list(rt.tenant_id, status=status_filter, limit=max(1, min(limit, 500)))
        return JSONResponse(
            status_code=200,
            content={
                "executions": [
                    {
                        "execution_id": str(r.execution.id),
                        "status": r.execution.status.value,
                        "strategy": r.execution.strategy.value,
                        "created_at": r.execution.created_at.isoformat(),
                        "completed_at": r.execution.completed_at.isoformat()
                        if r.execution.completed_at
                        else None,
                    }
                    for r in rows
                ]
            },
        )

    return router
