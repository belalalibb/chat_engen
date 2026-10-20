"""Bind Remote-Gateway-backed providers (ADR-0008 data plane) from environment.

Reuses upstream's proven ``providers.real.gateway.RemoteGatewayAdapter`` — no
copy, no new dependency. One adapter instance == one gateway route token ==
one chat_engine provider.

Environment contract (same posture as upstream ``apps/composition/gateway.py``,
re-declared here because chat_engine never imports ``apps.*``):

    CE_GATEWAY_BASE_URL         gateway data-plane URL (https://, or http://
                                loopback for local dev). Absent => no gateway
                                providers are bound (honest default).
    CE_GATEWAY_SECRET           current shared secret           (required)
    CE_GATEWAY_SECRET_VERSION   positive integer                (required)
    CE_GATEWAY_ROUTES           "provider_key=route_token,provider_key2=token2"
                                one chat_engine provider per route (required)

Discovery is DATA, not guesswork: for every route the gateway's own
``GET /v1/describe`` projection supplies display name, declared operations,
capabilities and model names. A route whose describe fails is skipped with a
reason (never a silent half-registration). Secrets/tokens are stored once in
the runtime secret manager and reach the adapter through resolvers only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from core.contracts.domain import AuthType, Provider, ProviderStatus
from core.contracts.provider import ProviderCapabilities, ProviderOperation

if TYPE_CHECKING:
    from chat_engine.composition.runtime import Runtime

ENV_BASE_URL = "CE_GATEWAY_BASE_URL"
ENV_SECRET = "CE_GATEWAY_SECRET"
ENV_SECRET_VERSION = "CE_GATEWAY_SECRET_VERSION"
ENV_ROUTES = "CE_GATEWAY_ROUTES"

_LOOPBACK = ("http://localhost", "http://127.0.0.1")
_CAPABILITY_FIELDS = frozenset(ProviderCapabilities.model_fields)
#: Operations the platform contract knows; anything else the gateway declares is
#: ignored for routing (never invented, never crashes).
_KNOWN_OPERATIONS = {op.value: op for op in ProviderOperation}
_DESCRIBE_TIMEOUT_S = 15.0


@dataclass(frozen=True)
class GatewayBinding:
    """Validated env settings (secret/token values never appear in repr)."""

    base_url: str
    secret: str
    secret_version: int
    routes: dict[str, str]  # provider_key -> route token

    def __repr__(self) -> str:
        return (
            f"GatewayBinding(base_url={self.base_url!r}, secret_version={self.secret_version}, "
            f"routes={sorted(self.routes)}, secret='[SCRUBBED]', tokens='[SCRUBBED]')"
        )


@dataclass
class GatewayBindReport:
    bound: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # provider_key -> reason
    embed_only: list[str] = field(default_factory=list)  # CE-24/H: served via /v1/embeddings


def gateway_binding_from_env(env: Mapping[str, str]) -> GatewayBinding | None:
    base_url = env.get(ENV_BASE_URL, "").strip()
    if not base_url:
        return None
    if not base_url.startswith("https://") and not any(
        base_url == p or base_url.startswith(p + ":") or base_url.startswith(p + "/")
        for p in _LOOPBACK
    ):
        msg = f"{ENV_BASE_URL} must be https:// (plaintext only for loopback dev)"
        raise ValueError(msg)
    secret = env.get(ENV_SECRET, "").strip()
    version_raw = env.get(ENV_SECRET_VERSION, "").strip()
    if not secret or not version_raw:
        msg = f"{ENV_BASE_URL} is set but {ENV_SECRET}/{ENV_SECRET_VERSION} missing"
        raise ValueError(msg)
    if not version_raw.isdigit() or int(version_raw) < 1:
        msg = f"{ENV_SECRET_VERSION} must be a positive integer"
        raise ValueError(msg)
    routes: dict[str, str] = {}
    for pair in env.get(ENV_ROUTES, "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        key, sep, token = pair.partition("=")
        key, token = key.strip(), token.strip()
        if not sep or not key or not token:
            msg = f"{ENV_ROUTES} entries must be 'provider_key=route_token[|route_token2…]'"
            raise ValueError(msg)
        # CE-24/K: several route tokens for one provider, '|'-separated (rotation pool)
        routes[key] = token
    if not routes:
        msg = f"{ENV_BASE_URL} is set but {ENV_ROUTES} declares no routes"
        raise ValueError(msg)
    return GatewayBinding(
        base_url=base_url.rstrip("/"), secret=secret, secret_version=int(version_raw), routes=routes
    )


def _capabilities_from_describe(raw: Any) -> ProviderCapabilities:
    if not isinstance(raw, Mapping):
        return ProviderCapabilities()
    return ProviderCapabilities(**{k: bool(v) for k, v in raw.items() if k in _CAPABILITY_FIELDS})


def _operations_from_describe(raw: Any) -> list[ProviderOperation]:
    if not isinstance(raw, list):
        return []
    out: list[ProviderOperation] = []
    for item in raw:
        op = _KNOWN_OPERATIONS.get(str(item))
        if op is not None and op not in out:
            out.append(op)
    return out


def _models_from_describe(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for item in raw:
        name = item.get("name") if isinstance(item, Mapping) else item
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    return names


def _run(coro: Any) -> Any:
    """Run a coroutine from sync composition code (no running loop expected)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # A loop is already running (e.g. inside an async test): use a worker thread.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def bind_gateway_providers_from_env(rt: Runtime, env: Mapping[str, str]) -> GatewayBindReport:
    """Register one chat_engine provider per configured gateway route."""
    report = GatewayBindReport()
    binding = gateway_binding_from_env(env)
    if binding is None:
        return report

    from providers.real.gateway import (
        CREDENTIAL_MODE_PLATFORM,
        GatewaySecret,
        RemoteGatewayAdapter,
        build_gateway_manifest,
    )

    tenant = rt.tenant_id
    secret_ref = rt.secrets.store(tenant, binding.secret)
    version = binding.secret_version

    def _secret() -> GatewaySecret:
        return GatewaySecret(value=str(rt.secrets.resolve(tenant, secret_ref)), version=version)

    for provider_key, token in binding.routes.items():
        if provider_key in rt.provider_keys:
            report.skipped[provider_key] = "provider_key already registered"
            continue
        tokens = [t.strip() for t in token.split("|") if t.strip()]
        token_refs = [rt.secrets.store(tenant, t) for t in tokens]
        token_ref = token_refs[0]
        provider_id = uuid4()

        def _token(pid: UUID = provider_id, first: str = token_ref) -> str:
            # CE-24/K: read the ACTIVE ref so rotation applies to gateway routes too
            return str(rt.secrets.resolve(tenant, rt.credential_refs.get(pid, first)))

        # Probe manifest: declare generate_text only until describe tells us more.
        probe = RemoteGatewayAdapter(
            build_gateway_manifest(
                provider_key=provider_key,
                display_name=provider_key,
                operations=[ProviderOperation.GENERATE_TEXT],
                capabilities=ProviderCapabilities(chat=True),
            ),
            base_url=binding.base_url,
            gateway_secret_resolver=_secret,
            route_token_resolver=_token,
            credential_mode=CREDENTIAL_MODE_PLATFORM,
            default_timeout_ms=int(_DESCRIBE_TIMEOUT_S * 1000),
        )

        async def _describe(adapter: RemoteGatewayAdapter = probe) -> dict[str, Any] | None:
            try:
                return await adapter.describe()
            finally:
                await adapter.aclose()

        try:
            described = _run(_describe())
        except Exception as exc:
            report.skipped[provider_key] = f"describe failed: {type(exc).__name__}"
            continue
        if not described:
            report.skipped[provider_key] = "describe unreachable/denied (check secret, token, URL)"
            continue
        operations = _operations_from_describe(described.get("operations"))
        embed_only = (
            ProviderOperation.GENERATE_TEXT not in operations
            and ProviderOperation.CREATE_EMBEDDINGS in operations
        )
        if ProviderOperation.GENERATE_TEXT not in operations and not embed_only:
            report.skipped[provider_key] = "gateway route does not declare generate_text"
            continue
        models = _models_from_describe(described.get("models"))
        if not models:
            report.skipped[provider_key] = "gateway route declares no models"
            continue
        credential_mode = str(described.get("credential_mode") or CREDENTIAL_MODE_PLATFORM)
        if credential_mode != CREDENTIAL_MODE_PLATFORM:
            report.skipped[provider_key] = (
                f"credential_mode {credential_mode!r} needs BYOK (unsupported here)"
            )
            continue

        display_name = str(described.get("display_name") or provider_key)
        version_str = str(described.get("definition_version") or "1.0.0")
        manifest = build_gateway_manifest(
            provider_key=provider_key,
            display_name=display_name,
            operations=operations,
            capabilities=_capabilities_from_describe(described.get("capabilities")),
            static_models=models,
            definition_version=version_str if version_str.count(".") == 2 else "1.0.0",
        )
        # The gateway manifest ships status="disabled" (31 §19 step 13); the operator
        # enabled this route explicitly via env, which IS the admin decision here.
        manifest = manifest.model_copy(update={"status": "active"})
        adapter = RemoteGatewayAdapter(
            manifest,
            base_url=binding.base_url,
            gateway_secret_resolver=_secret,
            route_token_resolver=_token,
            credential_mode=CREDENTIAL_MODE_PLATFORM,
        )
        provider = Provider(
            id=provider_id,
            provider_key=provider_key,
            display_name=display_name,
            status=ProviderStatus.ACTIVE,
            auth_types=[AuthType.CUSTOM],
            supports_account_pool=len(token_refs) > 1,
        )
        rt.register_provider(
            provider=provider,
            manifest=manifest,
            adapter=adapter,
            credential_ref=token_ref,
            model_names=models,
        )
        if len(token_refs) > 1:
            pool = rt.extensions.get("credential_pool")
            if pool is not None:
                pool.register(provider_id, provider_key, token_refs)
        if embed_only:
            # CE-24/H: the upstream router only routes a provider for operations its
            # manifest declares, so an embed-only route is invisible to generate_text and
            # reachable through POST /v1/embeddings (operation=create_embeddings).
            embeds = rt.extensions.setdefault("embedding_routes", {})
            embeds[provider_key] = {"provider_id": str(provider_id), "models": list(models)}
            report.embed_only.append(provider_key)
        report.bound.append(provider_key)
    return report
