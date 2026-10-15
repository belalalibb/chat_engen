from __future__ import annotations

import os

import pytest
from chat_engine.api.app import create_app
from chat_engine.composition.runtime import build_runtime
from fastapi.testclient import TestClient

HERMETIC_ENV: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _no_live_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests are hermetic: never pick up a live key from the sandbox."""
    for k in ("GROQ_API_KEY", "GSK_API_KEY", "GSK_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert "GROQ_API_KEY" not in os.environ


@pytest.fixture
def runtime():
    return build_runtime(HERMETIC_ENV)


@pytest.fixture
def client(runtime) -> TestClient:
    return TestClient(create_app(runtime))
