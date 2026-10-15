"""CE-01/CE-02: upstream golden /v1/execute snapshots replay on chat_engine.

Volatile fields are normalised (COMPATIBILITY_MAP §2); everything else —
status codes, messages, details, result content — must match byte-for-byte.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

GOLDEN = sorted(
    (Path(__file__).resolve().parents[2] / "compat" / "golden" / "execute").glob("*.json")
)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
VOLATILE_KEYS = {"execution_id", "poll_url", "trace_id", "time", "decided_at", "created_at", "id"}


def normalise(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("<volatile>" if k in VOLATILE_KEYS else normalise(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [normalise(v) for v in value]
    if isinstance(value, str):
        return _UUID.sub("<uuid>", value)
    return value


def test_golden_count() -> None:
    assert len(GOLDEN) == 14


@pytest.mark.parametrize("path", GOLDEN, ids=[p.stem for p in GOLDEN])
def test_golden_replays(client, path: Path) -> None:
    doc = json.loads(path.read_text())
    r = client.post("/v1/execute", json=doc["request"])
    assert r.status_code == doc["status"], r.text
    assert normalise(r.json()) == normalise(doc["response"])
