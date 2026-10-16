"""CE-14 App Factory contracts.

Scope (explicit): spec → scaffold → review. Produces ARTIFACTS (files with refs) — never
executes generated code, never performs network calls from outputs. Steps marked
``requires_approval`` stop the job (``awaiting_approval``) until a human approves.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from core.contracts.base import BoundedStr, ContractModel, JsonObject
from pydantic import Field


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageSpec(ContractModel):
    key: BoundedStr  # spec | scaffold | review | custom
    model: BoundedStr | None = None  # canonical_id / model_key; None ⇒ AUTO
    instructions: str = Field(default="", max_length=100_000)
    requires_approval: bool = False
    output_name: BoundedStr | None = None  # artifact file name (e.g. SPEC.md)


DEFAULT_STAGES: list[StageSpec] = [
    StageSpec(
        key="spec",
        instructions=(
            "Write a concise product/technical specification for the requested application: "
            "goals, users, screens/endpoints, data model, non-goals, acceptance criteria. Markdown."
        ),
        output_name="SPEC.md",
    ),
    StageSpec(
        key="scaffold",
        instructions=(
            "From the specification above, produce a scaffold as a JSON object mapping relative "
            "file paths to file contents (strings). Include a README.md. Output ONLY the JSON object."
        ),
        output_name="scaffold.json",
        requires_approval=True,
    ),
    StageSpec(
        key="review",
        instructions=(
            "Review the specification and scaffold above. List defects, security concerns, missing "
            "pieces and concrete next steps. Markdown."
        ),
        output_name="REVIEW.md",
    ),
]


class GenerateRequest(ContractModel):
    name: BoundedStr
    brief: str = Field(min_length=1, max_length=100_000)
    project_id: UUID | None = None
    model: BoundedStr | None = None
    provider: BoundedStr | None = None
    stages: list[StageSpec] | None = None  # None ⇒ DEFAULT_STAGES
    auto_approve: bool = False  # tests/CI: skip the human gate
    constraints: list[BoundedStr] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


class ArtifactRef(ContractModel):
    ref: BoundedStr  # "artifact://<job_id>/<name>"
    name: BoundedStr
    size: int = Field(ge=0)
    sha256: BoundedStr
    content_type: BoundedStr = "text/plain"
    stage: BoundedStr


class StageRun(ContractModel):
    key: BoundedStr
    status: BoundedStr  # pending | running | awaiting_approval | succeeded | failed | skipped
    model: BoundedStr | None = None
    provider_key: BoundedStr | None = None
    model_key: BoundedStr | None = None
    execution_id: BoundedStr | None = None
    artifact: ArtifactRef | None = None
    extracted_files: int = 0
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    approved_by: BoundedStr | None = None
    approved_at: datetime | None = None


class AppJob(ContractModel):
    id: UUID
    tenant_id: UUID
    project_id: UUID | None = None
    name: BoundedStr
    status: JobStatus
    brief: str
    stages: list[StageRun]
    current_stage: int = 0
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    usage: JsonObject = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    metadata: JsonObject = Field(default_factory=dict)


class ApproveRequest(ContractModel):
    approved_by: BoundedStr = "owner"
    note: BoundedStr | None = None


__all__ = [
    "DEFAULT_STAGES",
    "AppJob",
    "ApproveRequest",
    "ArtifactRef",
    "GenerateRequest",
    "JobStatus",
    "StageRun",
    "StageSpec",
]
