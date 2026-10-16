"""AppFactoryRunner (CE-14): plan → chained HandoffPackets through the governed
``Orchestrator.run_step`` (one call per stage, previous outputs in-packet) → artifacts.

Boundaries: no generated code is executed; outputs are files only; a stage with
``requires_approval`` parks the job in ``awaiting_approval`` until ``approve()``.
Upstream ``core.agent.app_factory`` (``generation_deferred``) is untouched.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from core.contracts.base import JsonObject

from chat_engine.app_factory.artifacts import ArtifactStorePort
from chat_engine.app_factory.contracts import (
    DEFAULT_STAGES,
    AppJob,
    ArtifactRef,
    GenerateRequest,
    JobStatus,
    StageRun,
    StageSpec,
)
from chat_engine.conversations.service import ChatError
from chat_engine.util.time import utc_now

if TYPE_CHECKING:
    from chat_engine.composition.runtime import Runtime

MAX_SCAFFOLD_FILES = 200
MAX_FILE_BYTES = 512_000


class JobNotFound(LookupError):
    pass


class AppFactoryRunner:
    def __init__(self, rt: Runtime, store: ArtifactStorePort) -> None:
        self._rt = rt
        self._store = store
        self._jobs: dict[UUID, AppJob] = {}
        self._specs: dict[UUID, list[StageSpec]] = {}
        self._reqs: dict[UUID, GenerateRequest] = {}
        self._outputs: dict[UUID, list[tuple[str, str]]] = {}  # job → [(stage_key, text)]
        self._tasks: dict[UUID, asyncio.Task[None]] = {}

    @property
    def artifact_store(self) -> ArtifactStorePort:
        return self._store

    # -- public -----------------------------------------------------------------------------------

    def submit(self, req: GenerateRequest) -> AppJob:
        stages = list(req.stages) if req.stages else [s.model_copy() for s in DEFAULT_STAGES]
        now = utc_now()
        job = AppJob(
            id=uuid4(),
            tenant_id=self._rt.tenant_id,
            project_id=req.project_id,
            name=req.name,
            status=JobStatus.QUEUED,
            brief=req.brief,
            stages=[
                StageRun(key=s.key, status="pending", model=s.model or req.model) for s in stages
            ],
            created_at=now,
            updated_at=now,
            metadata=dict(req.metadata),
        )
        self._jobs[job.id] = job
        self._specs[job.id] = stages
        self._reqs[job.id] = req
        self._outputs[job.id] = []
        self._spawn(job.id)
        return job

    def _spawn(self, job_id: UUID) -> None:
        task = asyncio.create_task(self._run(job_id))
        self._tasks[job_id] = task
        self._rt.background_tasks.add(task)  # strong ref: never GC-cancelled mid-flight
        task.add_done_callback(self._rt.background_tasks.discard)

    def get(self, job_id: UUID) -> AppJob:
        job = self._jobs.get(job_id)
        if job is None or job.tenant_id != self._rt.tenant_id:
            raise JobNotFound(str(job_id))
        return job

    def list_jobs(self) -> list[AppJob]:
        return sorted(
            (j for j in self._jobs.values() if j.tenant_id == self._rt.tenant_id),
            key=lambda j: j.created_at,
        )

    def approve(self, job_id: UUID, *, approved_by: str) -> AppJob:
        job = self.get(job_id)
        if job.status is not JobStatus.AWAITING_APPROVAL:
            raise ChatError(
                "conflict", f"job is {job.status.value}, not awaiting approval", http_status=409
            )
        idx = job.current_stage
        stage = job.stages[idx].model_copy(
            update={"status": "pending", "approved_by": approved_by, "approved_at": utc_now()}
        )
        job = self._save(
            job.model_copy(
                update={
                    "status": JobStatus.QUEUED,
                    "stages": [*job.stages[:idx], stage, *job.stages[idx + 1 :]],
                }
            )
        )
        self._spawn(job.id)
        return job

    def cancel(self, job_id: UUID) -> AppJob:
        job = self._save(self.get(job_id).model_copy(update={"status": JobStatus.CANCELLED}))
        t = self._tasks.get(job_id)
        if t is not None and not t.done():
            t.cancel()
        return job

    async def drive(self, job_id: UUID) -> AppJob:
        """Resume a job whose background task was torn down by the host loop (e.g. test
        clients recreate loops per request). Stage index makes this idempotent."""
        job = self.get(job_id)
        t = self._tasks.get(job_id)
        if job.status in (JobStatus.QUEUED, JobStatus.RUNNING) and (t is None or t.done()):
            job = self._save(job.model_copy(update={"status": JobStatus.QUEUED}))
            await self._run(job_id)
            return self.get(job_id)
        return job

    async def wait(self, job_id: UUID, timeout: float = 30.0) -> AppJob:
        t = self._tasks.get(job_id)
        if t is not None:
            await asyncio.wait({t}, timeout=timeout)
        return self.get(job_id)

    # -- internals --------------------------------------------------------------------------------

    def _save(self, job: AppJob) -> AppJob:
        job = job.model_copy(update={"updated_at": utc_now()})
        self._jobs[job.id] = job
        return job

    def _set_stage(self, job: AppJob, idx: int, **upd: Any) -> AppJob:
        st = job.stages[idx].model_copy(update=upd)
        return self._save(
            job.model_copy(update={"stages": [*job.stages[:idx], st, *job.stages[idx + 1 :]]})
        )

    def _messages(self, job: AppJob, spec: StageSpec, req: GenerateRequest) -> list[JsonObject]:
        system = (
            "You are the chat_engine App Factory. You produce documents and file scaffolds only. "
            "Never include instructions to run commands or fetch network resources."
        )
        if req.constraints:
            system += "\nConstraints:\n" + "\n".join(f"- {c}" for c in req.constraints)
        msgs: list[JsonObject] = [{"role": "system", "content": system}]
        msgs.append({"role": "user", "content": f"Application: {job.name}\n\nBrief:\n{job.brief}"})
        for key, text in self._outputs[job.id]:
            msgs.append({"role": "assistant", "content": f"[{key}]\n{text}"})
        msgs.append({"role": "user", "content": f"Stage '{spec.key}': {spec.instructions}"})
        return msgs

    def _extract_scaffold(self, job_id: UUID, text: str, stage: str) -> list[ArtifactRef]:
        """scaffold stage: a JSON object {path: content} becomes individual artifacts."""
        raw = text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            raw = raw[raw.find("{") :] if "{" in raw else raw
        try:
            obj = json.loads(raw)
        except ValueError:
            return []
        if not isinstance(obj, dict):
            return []
        refs: list[ArtifactRef] = []
        for i, (path, content) in enumerate(obj.items()):
            if i >= MAX_SCAFFOLD_FILES or not isinstance(path, str):
                break
            data = (
                content if isinstance(content, str) else json.dumps(content, indent=2)
            ).encode()[:MAX_FILE_BYTES]
            try:
                refs.append(self._store.put(job_id, f"scaffold/{path}", data, stage=stage))
            except ValueError:
                continue  # unsafe path skipped, never written
        return refs

    async def _run(self, job_id: UUID) -> None:
        orch = self._rt.extensions["orchestrator"]
        req = self._reqs[job_id]
        specs = self._specs[job_id]
        job = self._save(self.get(job_id).model_copy(update={"status": JobStatus.RUNNING}))
        packet_id = f"appfactory:{job_id}"
        try:
            while job.current_stage < len(specs):
                idx = job.current_stage
                spec = specs[idx]
                run = job.stages[idx]
                if spec.requires_approval and not req.auto_approve and run.approved_by is None:
                    self._set_stage(job, idx, status="awaiting_approval")
                    self._save(
                        self.get(job_id).model_copy(update={"status": JobStatus.AWAITING_APPROVAL})
                    )
                    return
                job = self._set_stage(job, idx, status="running", started_at=utc_now())
                model = spec.model or req.model
                if model is None:  # AUTO: let the router pick
                    decision = self._rt.extensions["chat"].route(None, req.provider, [])
                    model = self._rt.models.get_by_id(decision.selected.model_id).model_key
                result = await orch.run_step(
                    model=model,
                    provider=req.provider,
                    messages=self._messages(job, spec, req),
                    ask=f"{spec.key}: {job.name}",
                    packet_id=packet_id,
                    conversation_id=None,
                )
                self._outputs[job_id].append((spec.key, result.text))
                name = spec.output_name or f"{spec.key}.md"
                ref = self._store.put(
                    job_id,
                    name,
                    result.text.encode(),
                    stage=spec.key,
                    content_type="application/json" if name.endswith(".json") else "text/markdown",
                )
                extracted = (
                    self._extract_scaffold(job_id, result.text, spec.key)
                    if spec.key == "scaffold"
                    else []
                )
                usage = dict(job.usage)
                for k, v in result.usage.items():
                    if isinstance(v, int | float):
                        usage[k] = float(usage.get(k, 0)) + v
                job = self._set_stage(
                    job,
                    idx,
                    status="succeeded",
                    finished_at=utc_now(),
                    provider_key=result.provider_key,
                    model_key=result.model_key,
                    execution_id=result.execution_id,
                    artifact=ref,
                    extracted_files=len(extracted),
                )
                job = self._save(
                    job.model_copy(
                        update={
                            "artifacts": [*job.artifacts, ref, *extracted],
                            "usage": usage,
                            "current_stage": idx + 1,
                        }
                    )
                )
            self._save(job.model_copy(update={"status": JobStatus.SUCCEEDED}))
        except asyncio.CancelledError:
            cur = self.get(job_id)
            if cur.status is not JobStatus.CANCELLED:  # host tore the loop down: resumable
                idx = min(cur.current_stage, len(cur.stages) - 1)
                self._set_stage(cur, idx, status="pending")
                self._save(self.get(job_id).model_copy(update={"status": JobStatus.QUEUED}))
            raise
        except ChatError as exc:
            job = self.get(job_id)
            self._set_stage(
                job,
                job.current_stage,
                status="failed",
                error=f"{exc.code}: {exc.message}",
                finished_at=utc_now(),
            )
            self._save(
                self.get(job_id).model_copy(
                    update={"status": JobStatus.FAILED, "error": f"{exc.code}: {exc.message}"}
                )
            )
        except Exception as exc:  # pragma: no cover — defensive: job must never hang
            job = self.get(job_id)
            self._set_stage(
                job, job.current_stage, status="failed", error=str(exc), finished_at=utc_now()
            )
            self._save(
                self.get(job_id).model_copy(update={"status": JobStatus.FAILED, "error": str(exc)})
            )


__all__ = ["AppFactoryRunner", "JobNotFound"]
