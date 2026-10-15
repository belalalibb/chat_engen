"""CE-01 guard: the INSTALLED upstream still matches the committed baseline.

Fails on any removal / rename / type / requiredness / enum / closed-set change
in the 14 core frozen modules. Upgrading the pinned SHA requires re-copying the
baseline (ledger decision) — this test is what makes that explicit.
"""

from __future__ import annotations

import json
from pathlib import Path

from chat_engine import UPSTREAM_SHA
from chat_engine.compat.freeze import APP_LAYER_MODULES, derive_core_modules

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "compat" / f"upstream_freeze_baseline_{UPSTREAM_SHA[:7]}.json"


def _load() -> dict:
    return json.loads(BASELINE.read_text())


def test_baseline_file_matches_pinned_sha() -> None:
    assert BASELINE.exists(), f"missing {BASELINE.name}: re-copy from upstream @ {UPSTREAM_SHA}"
    doc = _load()
    assert doc["schema"] == "qevion.contract_freeze_baseline/1"
    assert len(doc["frozen_modules"]) == 15
    assert len(doc["served_routes_v1"]) == 46


def test_core_frozen_modules_unchanged() -> None:
    doc = _load()
    derived = derive_core_modules(doc["frozen_modules"])
    assert len(derived) == 14
    drift: list[str] = []
    for name, expected in doc["modules"].items():
        if name in APP_LAYER_MODULES:
            continue
        actual = derived[name]
        for cname, cshape in expected["contracts"].items():
            if cname not in actual["contracts"]:
                drift.append(f"{name}.{cname}: REMOVED")
            elif actual["contracts"][cname] != cshape:
                drift.append(f"{name}.{cname}: CHANGED")
        for k, v in expected["constants"].items():
            if actual["constants"].get(k) != v:
                drift.append(f"{name}.{k}: constant changed")
    assert not drift, "upstream contract drift vs baseline:\n" + "\n".join(drift)


def test_contract_count_matches_record() -> None:
    """Upstream's record says 116 (measured at 9831c81b); the @0a13556 baseline
    carries 117 — one additive contract landed in R190. We pin what the FILE says."""
    doc = _load()
    total = sum(len(m["contracts"]) for m in doc["modules"].values())
    assert total == 117
