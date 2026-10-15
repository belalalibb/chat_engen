"""CE-02: /v1/models and the async execution lifecycle match upstream goldens."""

from __future__ import annotations

import json
from pathlib import Path

from tests.compat.test_golden_execute import normalise

GOLDEN = Path(__file__).resolve().parents[2] / "compat" / "golden"


def test_models_listing_matches_upstream(client) -> None:
    expected = json.loads((GOLDEN / "models" / "list_in_memory.json").read_text())
    actual = client.get("/v1/models").json()
    assert normalise(actual) == normalise(expected)


def test_async_status_matches_upstream(client) -> None:
    expected = json.loads((GOLDEN / "events" / "async_status.json").read_text())
    acc = client.post(
        "/v1/execute", json={"ask": "hello async", "execution_policy": {"async": True}}
    )
    assert acc.status_code == 202
    execution_id = acc.json()["execution_id"]
    # the in-process task completes on the same loop; TestClient drives it
    status = client.get(f"/v1/executions/{execution_id}")
    for _ in range(50):
        if status.json()["status"] == "succeeded":
            break
        import time

        time.sleep(0.02)
        status = client.get(f"/v1/executions/{execution_id}")
    assert normalise(status.json()) == normalise(expected)


def test_async_events_match_upstream(client) -> None:
    expected = (GOLDEN / "events" / "async_lifecycle.sse").read_text()
    acc = client.post(
        "/v1/execute", json={"ask": "hello async", "execution_policy": {"async": True}}
    )
    execution_id = acc.json()["execution_id"]
    with client.stream("GET", f"/v1/executions/{execution_id}/events") as resp:
        body = "".join(resp.iter_text())
    assert normalise(
        json.loads(
            "["
            + ",".join(line[6:] for line in body.splitlines() if line.startswith("data: "))
            + "]"
        )
    ) == normalise(
        json.loads(
            "["
            + ",".join(line[6:] for line in expected.splitlines() if line.startswith("data: "))
            + "]"
        )
    )
