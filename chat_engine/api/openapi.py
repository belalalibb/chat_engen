"""CE-10 — public OpenAPI with ``x-ce-stability`` per path.

- ``stable``: upstream-compatible routes (byte-compatible with general_ai_core)
  and the OpenAI-compatible surface.
- ``experimental``: chat_engine-native routes that may still change before v1.0.
- ``admin``: operator surfaces (BYOK, lifecycle, capabilities, admission).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

STABLE_PREFIXES = (
    "/v1/execute",
    "/v1/executions",
    "/v1/models",
    "/v1/events",
    "/v1/skills",
    "/v1/templates",
    "/v1/chat/completions",
    "/v1/openai/",
    "/healthz",
)
ADMIN_PREFIXES = ("/v1/admin/", "/v1/credentials")


def stability_for(path: str) -> str:
    if path.startswith(ADMIN_PREFIXES):
        return "admin"
    if path.startswith(STABLE_PREFIXES):
        return "stable"
    return "experimental"


def install_openapi(app: FastAPI, *, upstream_sha: str) -> None:
    def custom() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=(
                "chat_engine — standalone chat engine composed from general_ai_core "
                f"@ {upstream_sha[:7]}. Upstream-compatible routes are byte-compatible; "
                "OpenAI-compatible routes accept stock OpenAI SDKs."
            ),
            routes=app.routes,
        )
        schema["info"]["x-ce-upstream-sha"] = upstream_sha
        for path, item in schema.get("paths", {}).items():
            tag = stability_for(path)
            for op in item.values():
                if isinstance(op, dict):
                    op["x-ce-stability"] = tag
        app.openapi_schema = schema
        return schema

    app.openapi = custom  # type: ignore[method-assign]


__all__ = ["install_openapi", "stability_for"]
