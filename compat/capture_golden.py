#!/usr/bin/env python3
"""Capture golden /v1/execute request→response pairs from a running upstream
(general_ai_core in-memory profile, DEV_DEMO_PRINCIPAL=1) OR from chat_engine.

Usage: python compat/capture_golden.py http://localhost:8765 [--write]
Writes compat/golden/execute/<case>.json with {request, status, response}.
Volatile fields (execution_id, ids, timestamps, poll_url) are normalised by
the compat tests, not here — the raw capture stays honest.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "golden" / "execute"

CASES: dict[str, dict] = {
    "sync_ask": {"ask": "hello"},
    "sync_ask_with_output_format": {"ask": "hi", "output": {"format": "markdown"}},
    "explicit_model_known": {
        "ask": "hi",
        "model_policy": {"type": "explicit_model", "model_id": "local-echo-1"},
    },
    "explicit_model_unknown": {
        "ask": "hi",
        "model_policy": {"type": "explicit_model", "model_id": "gpt-nonexistent"},
    },
    "explicit_models_parallel_compare": {
        "ask": "hi",
        "model_policy": {
            "type": "explicit_models",
            "models": [{"model_id": "local-echo-1"}],
            "selection_strategy": "parallel_compare",
        },
    },
    "explicit_models_debate_rejected": {
        "ask": "hi",
        "model_policy": {
            "type": "explicit_models",
            "models": [{"model_id": "local-echo-1"}],
            "selection_strategy": "debate",
        },
    },
    "stream_rejected": {"ask": "hi", "execution_policy": {"stream": True}},
    "async_accepted": {"ask": "hello async", "execution_policy": {"async": True}},
    "validation_empty_ask": {"ask": ""},
    "unknown_field_rejected": {"ask": "hi", "bogus": 1},
    "explicit_policy_tag_rejected": {
        "ask": "hi",
        "model_policy": {"type": "explicit", "model_id": "local-echo-1"},
    },
    "tier_policy": {"ask": "hi", "model_policy": {"type": "tier", "tier": "medium"}},
    "auto_policy_with_fallback": {
        "ask": "hi",
        "model_policy": {"type": "auto", "allow_fallback": True, "fallback_scope": "same_model_different_provider"},
    },
    "skills_unknown_rejected": {"ask": "hi", "skills": ["no_such_skill"]},
}


def post(base: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        base + "/v1/execute",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def main(argv: list[str]) -> int:
    base = argv[0].rstrip("/")
    write = "--write" in argv
    OUT.mkdir(parents=True, exist_ok=True)
    for name, body in CASES.items():
        status, resp = post(base, body)
        doc = {"case": name, "request": body, "status": status, "response": resp}
        print(name, status)
        if write:
            (OUT / f"{name}.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
