"""ArtifactStore: local filesystem (default) — pluggable port for S3-compatible later.

Refs are opaque ``artifact://<job_id>/<name>``. Files are written, never executed. Path
traversal in names is rejected.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Protocol
from uuid import UUID

from chat_engine.app_factory.contracts import ArtifactRef

_SAFE = re.compile(r"^[A-Za-z0-9._\-/]+$")


def _safe_name(name: str) -> str:
    name = name.strip()
    if not name or name.startswith("/") or ".." in name.split("/") or not _SAFE.match(name):
        raise ValueError(f"unsafe artifact name: {name!r}")
    return name


class ArtifactStorePort(Protocol):
    backend_name: str

    def put(
        self, job_id: UUID, name: str, data: bytes, *, stage: str, content_type: str = "text/plain"
    ) -> ArtifactRef: ...
    def get(self, ref: str) -> bytes: ...
    def list(self, job_id: UUID) -> list[ArtifactRef]: ...


class LocalArtifactStore:
    backend_name = "local_fs"

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, ArtifactRef] = {}

    def _path(self, job_id: UUID, name: str) -> Path:
        p = (self._root / str(job_id) / _safe_name(name)).resolve()
        if self._root.resolve() not in p.parents:
            raise ValueError("artifact path escapes store root")
        return p

    def put(
        self, job_id: UUID, name: str, data: bytes, *, stage: str, content_type: str = "text/plain"
    ) -> ArtifactRef:
        p = self._path(job_id, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        ref = ArtifactRef(
            ref=f"artifact://{job_id}/{_safe_name(name)}",
            name=_safe_name(name),
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            content_type=content_type,
            stage=stage,
        )
        self._index[ref.ref] = ref
        return ref

    def get(self, ref: str) -> bytes:
        if not ref.startswith("artifact://"):
            raise KeyError(ref)
        job, _, name = ref[len("artifact://") :].partition("/")
        return self._path(UUID(job), name).read_bytes()

    def list(self, job_id: UUID) -> list[ArtifactRef]:
        prefix = f"artifact://{job_id}/"
        return sorted(
            (r for k, r in self._index.items() if k.startswith(prefix)), key=lambda r: r.name
        )


class InMemoryArtifactStore:
    backend_name = "in_memory"

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}
        self._index: dict[str, ArtifactRef] = {}

    def put(
        self, job_id: UUID, name: str, data: bytes, *, stage: str, content_type: str = "text/plain"
    ) -> ArtifactRef:
        name = _safe_name(name)
        ref = ArtifactRef(
            ref=f"artifact://{job_id}/{name}",
            name=name,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            content_type=content_type,
            stage=stage,
        )
        self._blobs[ref.ref] = data
        self._index[ref.ref] = ref
        return ref

    def get(self, ref: str) -> bytes:
        return self._blobs[ref]

    def list(self, job_id: UUID) -> list[ArtifactRef]:
        prefix = f"artifact://{job_id}/"
        return sorted(
            (r for k, r in self._index.items() if k.startswith(prefix)), key=lambda r: r.name
        )


__all__ = ["ArtifactStorePort", "InMemoryArtifactStore", "LocalArtifactStore"]
