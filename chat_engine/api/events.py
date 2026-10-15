"""``GET /v1/executions/{id}/events`` — upstream's six lifecycle events over SSE.

Upstream serves these by polling the execution store; so do we. Event
vocabulary is the frozen ``StreamEvent`` set — token deltas are NOT here
(they live on /v1/chat/stream, CE-08).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

from core.contracts.errors import ErrorCode
from core.contracts.execute import ExecutionStatus
from fastapi import APIRouter, Response
from fastapi.responses import StreamingResponse

from chat_engine.api.errors import error_response
from chat_engine.composition.runtime import Runtime
from chat_engine.store.executions import ExecutionNotFound

TERMINAL = {ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED}


def _sse(obj: dict[str, object]) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/executions/{execution_id}/events")
    async def events(execution_id: str) -> Response:
        try:
            eid = UUID(execution_id)
            rt.executions.get(rt.tenant_id, eid)
        except (ValueError, ExecutionNotFound):
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "Unknown execution.",
                details={"field": "execution_id"},
                http_status=404,
            )

        async def gen() -> AsyncIterator[str]:
            yield _sse({"execution_id": str(eid), "type": "execution_started"})
            seen: set[str] = set()
            for _ in range(600):  # ≤ 60s at 100ms
                report = rt.executions.get(rt.tenant_id, eid)
                for node_report in report.nodes:
                    key = node_report.node.node_key
                    if key not in seen:
                        seen.add(key)
                        yield _sse({"node": key, "type": "node_started"})
                        if node_report.node.status.value == "succeeded":
                            yield _sse({"node": key, "type": "node_completed"})
                        elif node_report.node.status.value == "failed":
                            yield _sse({"node": key, "type": "node_failed"})
                if report.execution.status in TERMINAL:
                    if (
                        report.execution.status is ExecutionStatus.SUCCEEDED
                        and report.final_output is not None
                    ):
                        yield _sse({"result": report.final_output, "type": "final"})
                    else:
                        yield _sse({"type": "error", "status": report.execution.status.value})
                    return
                await asyncio.sleep(0.1)
            yield _sse({"type": "error", "status": "timeout"})

        return StreamingResponse(gen(), media_type="text/event-stream")

    return router
