"""Process-local execution report store (chat_engine's own — upstream's lives
in ``apps.api.store`` which D0 forbids importing). Same semantics: reads
require the caller's tenant; cross-tenant == missing (20 §6)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from core.contracts.execute import ExecutionStatus
from core.execution.service import ExecutionReport


class ExecutionNotFound(LookupError):
    def __init__(self, execution_id: UUID) -> None:
        self.execution_id = execution_id
        super().__init__(f"execution not found: {execution_id}")


class InMemoryExecutionStore:
    def __init__(self) -> None:
        self._reports: dict[UUID, ExecutionReport] = {}

    def put(self, report: ExecutionReport) -> None:
        self._reports[report.execution.id] = report

    def get(self, tenant_id: UUID, execution_id: UUID) -> ExecutionReport:
        report = self._reports.get(execution_id)
        if report is None or report.execution.tenant_id != tenant_id:
            raise ExecutionNotFound(execution_id)
        return report

    def list(
        self,
        tenant_id: UUID,
        *,
        status: ExecutionStatus | None = None,
        initiated_by: UUID | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[ExecutionReport, ...]:
        rows = [
            r
            for r in self._reports.values()
            if r.execution.tenant_id == tenant_id
            and (status is None or r.execution.status is status)
            and (initiated_by is None or r.execution.user_id == initiated_by)
            and (created_after is None or r.execution.created_at > created_after)
            and (created_before is None or r.execution.created_at < created_before)
        ]
        rows.sort(key=lambda r: r.execution.created_at, reverse=True)
        if limit is not None:
            rows = rows[:limit]
        return tuple(rows)

    def __len__(self) -> int:
        return len(self._reports)
