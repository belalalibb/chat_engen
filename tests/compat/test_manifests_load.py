"""CE-01: every upstream manifest snapshot still validates against ProviderManifest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from core.contracts.provider import ProviderManifest

MANIFESTS = sorted((Path(__file__).resolve().parents[2] / "compat" / "manifests").glob("*.json"))


def test_manifest_count() -> None:
    assert len(MANIFESTS) == 14  # 12 templates + groq + genspark_llm


@pytest.mark.parametrize("path", MANIFESTS, ids=[p.stem for p in MANIFESTS])
def test_manifest_loads(path: Path) -> None:
    manifest = ProviderManifest.model_validate(json.loads(path.read_text()))
    assert manifest.id == path.stem
    if manifest.is_template:
        assert manifest.is_functional is False
        assert manifest.real_provider_required is True
    else:
        assert manifest.auth.types
