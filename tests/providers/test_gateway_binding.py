"""Hermetic tests for the remote-gateway provider binding (no network)."""

from __future__ import annotations

import json

import httpx
import pytest
from chat_engine.composition.runtime import build_runtime
from chat_engine.providers import gateway as gw


def test_binding_absent_without_base_url() -> None:
    assert gw.gateway_binding_from_env({}) is None


def test_binding_rejects_half_configuration() -> None:
    with pytest.raises(ValueError):
        gw.gateway_binding_from_env({"CE_GATEWAY_BASE_URL": "https://gw.example"})
    with pytest.raises(ValueError):
        gw.gateway_binding_from_env(
            {
                "CE_GATEWAY_BASE_URL": "http://evil.example",  # plaintext non-loopback
                "CE_GATEWAY_SECRET": "x" * 20,
                "CE_GATEWAY_SECRET_VERSION": "1",
                "CE_GATEWAY_ROUTES": "a=b",
            }
        )


def test_binding_parses_routes_and_scrubs_repr() -> None:
    b = gw.gateway_binding_from_env(
        {
            "CE_GATEWAY_BASE_URL": "http://127.0.0.1:8800/",
            "CE_GATEWAY_SECRET": "s3cr3t-value-0123456789",
            "CE_GATEWAY_SECRET_VERSION": "2",
            "CE_GATEWAY_ROUTES": "overchat=tok-A, deepai=tok-B",
        }
    )
    assert b is not None
    assert b.base_url == "http://127.0.0.1:8800"
    assert b.routes == {"overchat": "tok-A", "deepai": "tok-B"}
    assert "tok-A" not in repr(b) and "s3cr3t" not in repr(b)


def _fake_gateway(handler):
    """Patch the upstream adapter so its pooled client uses a mock transport."""
    from providers.real.gateway import adapter as upstream

    transport = httpx.MockTransport(handler)
    original_init = upstream.RemoteGatewayAdapter.__init__

    def patched(self, manifest, **kw):  # type: ignore[no-untyped-def]
        kw["transport"] = transport
        original_init(self, manifest, **kw)

    return upstream.RemoteGatewayAdapter, patched, original_init


def test_bind_registers_described_route_and_generates(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["X-Gateway-Secret"] == "s3cr3t-value-0123456789"
        assert req.headers["X-Route-Token"] in {"tok-A", "tok-dead"}
        if req.headers["X-Route-Token"] == "tok-dead":
            return httpx.Response(404, json={"error": {"category": "provider_unavailable"}})
        if req.url.path == "/v1/describe":
            return httpx.Response(
                200,
                json={
                    "display_name": "Overchat AI",
                    "credential_mode": "platform",
                    "capabilities": {"chat": True, "vision_input": True, "bogus": True},
                    "operations": ["generate_text", "analyze_vision", "not_an_op"],
                    "models": [
                        {"name": "gpt-6-luna", "context_window": 128000},
                        {"name": "gpt-5-nano"},
                    ],
                    "definition_version": "1.1.0",
                    "health_supported": False,
                },
            )
        if req.url.path == "/v1/execute":
            body = json.loads(req.content)
            seen["envelope"] = body
            return httpx.Response(
                200,
                json={
                    "succeeded": True,
                    "output": {"content": f"echo:{body['payload']['messages'][-1]['content']}"},
                    "usage": {"input_tokens": 3, "output_tokens": 2, "units": 1},
                    "latency_ms": 5,
                    "error": None,
                },
            )
        return httpx.Response(404)

    cls, patched, original = _fake_gateway(handler)
    monkeypatch.setattr(cls, "__init__", patched)
    try:
        rt = build_runtime(
            {
                "CE_GATEWAY_BASE_URL": "http://127.0.0.1:8800",
                "CE_GATEWAY_SECRET": "s3cr3t-value-0123456789",
                "CE_GATEWAY_SECRET_VERSION": "1",
                "CE_GATEWAY_ROUTES": "overchat=tok-A,dead=tok-dead",
            }
        )
    finally:
        monkeypatch.setattr(cls, "__init__", original)

    report = rt.extensions["gateway_report"]
    assert report["bound"] == ["overchat"]
    assert "dead" in report["skipped"]
    assert "overchat" in rt.provider_keys
    keys = {m.model_key for m in rt.models.active_models()}
    assert {"gpt-6-luna", "gpt-5-nano"} <= keys

    # end-to-end through the public chat API (sync path)
    from chat_engine.api.app import create_app
    from fastapi.testclient import TestClient

    client = TestClient(create_app(rt))
    r = client.post("/v1/chat", json={"message": "hello gateway", "model": "gpt-6-luna"})
    assert r.status_code == 200, r.text
    turn = r.json()["turn"]
    assert turn["provider_key"] == "overchat"
    assert "echo:hello gateway" in "".join(p.get("text", "") for p in turn["parts"])
    env = seen["envelope"]
    assert env["operation"] == "generate_text" and env["model"] == "gpt-6-luna"
    assert env["credential"] == {"mode": "platform"}
