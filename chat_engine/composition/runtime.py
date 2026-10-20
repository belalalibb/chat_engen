"""chat_engine composition root (D3): composes upstream ``core.*`` directly.

Never imports ``apps.*``. One process, in-memory by default (ADR-0014 posture
inherited). Real providers (Groq / Genspark LLM / any OpenAI-compatible) are
bound only when their keys are present in the environment — keys go through
``InMemorySecretManager`` and adapters receive an opaque ``credential_ref``
(20 §5). The hermetic ``local_echo`` provider is always bound so the engine
is exercisable with zero credentials and never pretends a real model answered.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from core.context.composer import ContextComposer
from core.contracts.domain import (
    AuthType,
    BindingAvailability,
    CredentialStatus,
    Modality,
    Model,
    ModelStatus,
    ModelTier,
    Provider,
    ProviderModelBinding,
    ProviderStatus,
)
from core.contracts.provider import (
    CredentialHealth,
    DiscoveredModel,
    HealthScope,
    ProviderCapabilities,
    ProviderError,
    ProviderErrorCategory,
    ProviderGenerateRequest,
    ProviderGenerateResponse,
    ProviderHealth,
    ProviderHealthState,
    ProviderManifest,
)
from core.contracts.routing import RoutingDecision
from core.execution.multi_model import MultiModelExecutor
from core.execution.service import ExecutionService
from core.memory.memory import InMemoryConversationStore, InMemoryMemoryStore
from core.providers.ports import ProviderAdapterPort
from core.providers.registry import BindingRegistry, ModelRegistry, ProviderRegistry
from core.roles.registry import RoleRegistry, SkillRegistry
from core.routing.capacity import ResourceSignalBoard
from core.routing.router import SimpleScoringRouter
from core.secrets.memory import InMemorySecretManager
from core.usage.memory import InMemoryUsageAccounting

from chat_engine import UPSTREAM_SHA
from chat_engine.store.executions import InMemoryExecutionStore
from chat_engine.streaming.contracts import StreamChunk

#: Demo tenant/user for the keyless local profile (same posture as upstream's
#: DEV_DEMO_PRINCIPAL=1 — explicit, never silently on in a durable profile).
DEMO_TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")
DEMO_USER_ID = UUID("00000000-0000-4000-8000-000000000002")
DEMO_TASK_UNITS = 1_000_000.0


# --- hermetic provider ------------------------------------------------------------


def echo_manifest() -> ProviderManifest:
    return ProviderManifest.model_validate(
        {
            "id": "local_echo",
            "name": "Local Echo (hermetic)",
            "version": "1.0.0",
            "status": "active",
            "auth": {"types": ["api_key"], "supports_refresh": False},
            "account_pool": {"supported": False},
            "capabilities": {"chat": True},
            "operations": ["generate_text"],
            "models": {"discovery": "static", "static_models": ["local-echo-1"]},
            "rate_limits": {"strategy": "provider_defined"},
            "health": {"checks": ["ping"]},
            "errors": {"mapping": "inline"},
        }
    )


class LocalEchoAdapter:
    """Hermetic ProviderAdapterPort — output mirrors upstream's local_echo
    byte-for-byte so golden snapshots captured from upstream replay here."""

    def __init__(self, manifest: ProviderManifest) -> None:
        self._manifest = manifest

    def get_manifest(self) -> ProviderManifest:
        return self._manifest

    async def validate_credential(self, credential_ref: str) -> CredentialHealth:
        return CredentialHealth(credential_ref=credential_ref, status=CredentialStatus.ACTIVE)

    async def discover_models(self, account_id: UUID | None = None) -> list[DiscoveredModel]:
        return []

    async def get_capabilities(self) -> ProviderCapabilities:
        return self._manifest.capabilities

    async def generate(self, request: ProviderGenerateRequest) -> ProviderGenerateResponse:
        ask = request.payload.get("ask", "")
        output: dict[str, object] = {
            "provider": "local-echo",
            "note": "hermetic local adapter — no real model was called",
            "echo": ask,
        }
        context = request.payload.get("context")
        if isinstance(context, dict):
            blocks = context.get("context_blocks")
            if isinstance(blocks, list):
                output["context_blocks"] = [
                    {"type": b.get("type"), "content": b.get("content"), "source": b.get("source")}
                    for b in blocks
                    if isinstance(b, dict)
                ]
        return ProviderGenerateResponse(
            request_id=request.request_id,
            succeeded=True,
            output=output,
            usage={"units": 1},
            latency_ms=0,
        )

    async def health_check(self, scope: HealthScope) -> ProviderHealth:
        return ProviderHealth(provider_id=self._manifest.id, state=ProviderHealthState.HEALTHY)

    def normalize_error(self, error: object) -> ProviderError:
        return ProviderError(
            category=ProviderErrorCategory.NON_RETRYABLE_ERROR,
            retryable=False,
            safe_message="local echo failure",
        )


class StreamingEchoAdapter(LocalEchoAdapter):
    """Hermetic adapter that ALSO implements StreamingAdapterPort — streams the
    ask word by word. Scriptable failures make the five D7 cases testable."""

    def __init__(self, manifest: ProviderManifest) -> None:
        super().__init__(manifest)
        self.fail_after_words: int | None = None  # fail mid-stream (case 4)
        self.fail_before_first: bool = False  # fail before any delta (case 3)
        self.delay_s: float = 0.0

    async def stream(self, request: ProviderGenerateRequest) -> AsyncIterator[StreamChunk]:
        from core.contracts.provider import ProviderError as _PE

        if self.fail_before_first:
            yield StreamChunk(
                error=_PE(
                    category=ProviderErrorCategory.PROVIDER_UNAVAILABLE,
                    retryable=True,
                    safe_message="scripted pre-delta failure",
                )
            )
            return
        ask = str(request.payload.get("ask", ""))
        # Honour a resume continuation: messages[] ending with an assistant
        # prefix + "Continue" ⇒ stream only the remainder (a real model would).
        msgs = request.payload.get("messages")
        if isinstance(msgs, list) and len(msgs) >= 2:
            prev = msgs[-2] if isinstance(msgs[-2], dict) else {}
            last = msgs[-1] if isinstance(msgs[-1], dict) else {}
            if prev.get("role") == "assistant" and "Continue" in str(last.get("content", "")):
                prefix = str(prev.get("content", ""))
                if ask.startswith(prefix):
                    ask = ask[len(prefix) :].lstrip(" ")
        words = ask.split(" ")
        for i, w in enumerate(words):
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            if self.fail_after_words is not None and i >= self.fail_after_words:
                yield StreamChunk(
                    error=_PE(
                        category=ProviderErrorCategory.RETRYABLE_SERVER_ERROR,
                        retryable=True,
                        safe_message="scripted mid-stream failure",
                    )
                )
                return
            yield StreamChunk(delta=(w if i == 0 else " " + w))
        yield StreamChunk(
            usage={
                "prompt_tokens": len(words),
                "completion_tokens": len(words),
                "total_tokens": 2 * len(words),
            }
        )
        yield StreamChunk(finish_reason="stop")


def model_row(
    key: str, *, tier: ModelTier = ModelTier.MEDIUM, context_window: int | None = None
) -> Model:
    """Registry Model row for a provider-declared model name (never invented)."""
    return Model(
        id=uuid4(),
        model_key=key,
        display_name=key,
        tier=tier,
        modalities=[Modality.TEXT],
        capabilities=["reasoning"],
        context_window=context_window,
        quality_score=0.8,
        reliability_score=0.8,
        cost_score=0.8,
        speed_score=0.8,
        status=ModelStatus.ACTIVE,
    )


# --- runtime ------------------------------------------------------------------------


@dataclass
class Runtime:
    """Everything the API layer needs. Built once per process by ``build_runtime``."""

    providers: ProviderRegistry
    models: ModelRegistry
    bindings: BindingRegistry
    adapters: MutableMapping[UUID, ProviderAdapterPort]
    credential_refs: MutableMapping[UUID, str]
    signals: ResourceSignalBoard
    router: SimpleScoringRouter
    execution: ExecutionService
    multi_model: MultiModelExecutor
    usage: InMemoryUsageAccounting
    secrets: InMemorySecretManager
    executions: InMemoryExecutionStore
    conversations: InMemoryConversationStore
    memory: InMemoryMemoryStore
    roles: RoleRegistry
    skills: SkillRegistry
    composer: ContextComposer
    idempotency: dict[tuple[UUID, str], UUID] = field(default_factory=dict)
    provider_keys: tuple[str, ...] = ()
    profile: str = "in-memory"
    context_budget: int = 16_000
    tenant_id: UUID = DEMO_TENANT_ID
    user_id: UUID = DEMO_USER_ID
    # Extension slots filled by later tasks (catalog, admission, capabilities,
    # streaming, conversations, skills). Typed loosely on purpose: each CE task
    # owns its slot's type; the API layer only reads what it composes.
    extensions: dict[str, Any] = field(default_factory=dict)
    background_tasks: set[asyncio.Task[None]] = field(default_factory=set)

    @property
    def upstream_sha(self) -> str:
        return UPSTREAM_SHA

    def reorder_decision(self, decision: RoutingDecision) -> RoutingDecision:
        """CE-24/C: apply live-signal reordering (no-op unless enabled)."""
        live = self.extensions.get("live_signals")
        index = self.extensions.get("catalog_index")
        if live is None or index is None or not getattr(live, "enabled", False):
            return decision
        out: RoutingDecision = live.reorder(decision, index.provider_key)
        return out

    def register_provider(
        self,
        *,
        provider: Provider,
        manifest: ProviderManifest,
        adapter: ProviderAdapterPort,
        credential_ref: str,
        model_names: list[str],
        tier: ModelTier = ModelTier.MEDIUM,
    ) -> list[Model]:
        """Register a provider + its models + bindings in the ONE registry set.

        Models already known by ``model_key`` are reused (same logical model,
        another provider — R190) instead of duplicated.
        """
        self.providers.register(provider, manifest)
        self.adapters[provider.id] = adapter
        self.credential_refs[provider.id] = credential_ref
        rows: list[Model] = []
        for name in model_names:
            try:
                model = self.models.get(name)
            except Exception:
                model = model_row(name, tier=tier)
                self.models.register(model)
            self.bindings.register(
                ProviderModelBinding(
                    provider_id=provider.id,
                    model_id=model.id,
                    provider_model_name=name,
                    availability=BindingAvailability.AVAILABLE,
                )
            )
            rows.append(model)
        self.provider_keys = (*self.provider_keys, provider.provider_key)
        index = self.extensions.get("catalog_index")
        if index is not None:
            index.rebuild()
        ledger = self.extensions.get("capabilities")
        if ledger is not None:
            ledger.declare_from_manifest(provider.provider_key, manifest.capabilities)
        admission = self.extensions.get("admission")
        if admission is not None:
            from chat_engine.admission import Limits

            meta = next(
                (b.limits_metadata for b in self.bindings.bindings_for_provider(provider.id)), None
            )
            admission.set_limits(f"provider:{provider.provider_key}", Limits.from_metadata(meta))
        return rows

    def unregister_provider(self, provider_key: str) -> None:
        """Remove a provider + its bindings (models stay; a model with no bindings
        simply becomes unavailable — upstream's own projection rule)."""
        entry = self.providers.get(provider_key)
        pid = entry.provider.id
        for binding in list(self.bindings.bindings_for_provider(pid)):
            self.bindings.remove(binding.provider_id, binding.model_id)
        self.providers.remove(provider_key)
        self.adapters.pop(pid, None)
        self.credential_refs.pop(pid, None)
        self.provider_keys = tuple(k for k in self.provider_keys if k != provider_key)
        index = self.extensions.get("catalog_index")
        if index is not None:
            index.rebuild()
        ledger = self.extensions.get("capabilities")
        if ledger is not None:
            ledger.forget_provider(provider_key)


def _bind_echo(rt: Runtime) -> None:
    manifest = echo_manifest()
    provider = Provider(
        id=uuid4(),
        provider_key="local_echo",
        display_name="Local Echo (hermetic)",
        status=ProviderStatus.ACTIVE,
        auth_types=[AuthType.API_KEY],
        supports_account_pool=False,
    )
    rt.register_provider(
        provider=provider,
        manifest=manifest,
        adapter=LocalEchoAdapter(manifest),
        credential_ref="secret-ref://local-echo",
        model_names=["local-echo-1"],
    )


def build_runtime(environ: Mapping[str, str] | None = None) -> Runtime:
    """Compose the engine. ``environ`` injectable for hermetic tests."""
    env = os.environ if environ is None else environ
    providers = ProviderRegistry()
    models = ModelRegistry()
    bindings = BindingRegistry()
    adapters: dict[UUID, ProviderAdapterPort] = {}
    credential_refs: dict[UUID, str] = {}
    signals = ResourceSignalBoard()
    router = SimpleScoringRouter(providers, models, bindings, signals=signals)
    usage = InMemoryUsageAccounting()
    usage.configure_tenant(DEMO_TENANT_ID, plan="local", task_units_limit=DEMO_TASK_UNITS)
    execution = ExecutionService(
        adapters=adapters,
        credential_refs=credential_refs,
        bindings=bindings,
        usage=usage,
        signals=signals,
    )
    multi_model = MultiModelExecutor(router=router, execution=execution)
    conversations = InMemoryConversationStore()
    memory = InMemoryMemoryStore()
    roles = RoleRegistry()
    skills = SkillRegistry()
    composer = ContextComposer(memory, conversations, roles)
    rt = Runtime(
        providers=providers,
        models=models,
        bindings=bindings,
        adapters=adapters,
        credential_refs=credential_refs,
        signals=signals,
        router=router,
        execution=execution,
        multi_model=multi_model,
        usage=usage,
        secrets=InMemorySecretManager(),
        executions=InMemoryExecutionStore(),
        conversations=conversations,
        memory=memory,
        roles=roles,
        skills=skills,
        composer=composer,
        context_budget=int(env.get("CE_CONTEXT_BUDGET", "16000")),
    )
    _bind_echo(rt)
    from chat_engine.handoff import HandoffCompiler

    rt.extensions["handoff_compiler"] = HandoffCompiler()
    # CE-04: read-side index over the registries + canonical identity service
    from chat_engine.catalog import CatalogIndex, CatalogService

    index = CatalogIndex(providers, models, bindings)
    rt.extensions["catalog_index"] = index
    rt.extensions["catalog"] = CatalogService(index, signals)
    # CE-05: provider lifecycle (D2) + BYOK credentials by reference
    from chat_engine.credentials import CredentialService
    from chat_engine.providers.lifecycle import ProviderLifecycleService

    rt.extensions["lifecycle"] = ProviderLifecycleService(rt)
    rt.extensions["credentials"] = CredentialService(
        rt.secrets, tenant_id=rt.tenant_id, user_id=rt.user_id
    )
    # CE-06: capability evidence ledger (side registry) + probes
    from chat_engine.capabilities import CapabilityLedger
    from chat_engine.capabilities.probes import register_default_probes

    ledger = CapabilityLedger()
    for key in rt.provider_keys:
        ledger.declare_from_manifest(key, rt.providers.get(key).manifest.capabilities)
    # the hermetic echo provider is verified by construction (it IS the test double)
    ledger.record_external_verification("local_echo", "chat", passed=True, detail="hermetic")
    register_default_probes(rt, ledger)
    rt.extensions["capabilities"] = ledger
    # CE-07: admission (in-memory default; Redis when ADMISSION_REDIS_URL is set)
    from chat_engine.admission import BreakerPolicy, IdempotencyStore, InMemoryAdmission, Limits

    redis_url = env.get("ADMISSION_REDIS_URL", "").strip()
    if redis_url:
        from chat_engine.admission.redis_backend import RedisAdmission

        rt.extensions["admission"] = RedisAdmission(redis_url)
    else:
        # CE-24/B: per-category breaker rules; unset ⇒ legacy relative breaker (5/10/30s)
        rt.extensions["admission"] = InMemoryAdmission(
            breaker_policy=BreakerPolicy.from_env(env.get("CE_BREAKER_POLICY_JSON"))
        )
    # Tenant-wide ceiling (CE_TENANT_RPM / CE_TENANT_CONCURRENCY). Provider scopes keep
    # their own (conservative or metadata) limits; the tenant scope only guards the
    # process as a whole, so its default is wider than one provider's 60 rpm.
    tenant_rpm = int(env.get("CE_TENANT_RPM", "600") or 600)
    tenant_conc = int(env.get("CE_TENANT_CONCURRENCY", "16") or 16)
    rt.extensions["admission"].set_limits(
        f"tenant:{rt.tenant_id}",
        Limits(rpm=max(1, tenant_rpm), concurrency=max(1, tenant_conc), source="tenant_env"),
    )
    rt.extensions["idempotency"] = IdempotencyStore()
    # CE-24/K: credential rotation pool (no-op for single-credential providers)
    from chat_engine.credentials.pool import CredentialPool

    rt.extensions["credential_pool"] = CredentialPool(rt.credential_refs)
    # CE-24/D: virtual keys (off by default — anonymous requests behave exactly as before)
    from chat_engine.auth import AuthMode, KeyService

    rt.extensions["keys"] = KeyService(
        tenant_id=rt.tenant_id,
        mode=AuthMode.from_env(env.get("CE_AUTH_MODE")),
        master_key=(env.get("CE_MASTER_KEY") or "").strip() or None,
        admission=rt.extensions["admission"],
    )
    # CE-08: streaming service (optional port; provider must have verified streaming_text)
    from chat_engine.streaming.service import StreamService

    rt.extensions["streaming"] = StreamService(rt)
    # CE-09: conversations/projects/turns + sync /v1/chat over HandoffPacket
    from chat_engine.conversations import ChatService
    from chat_engine.conversations import InMemoryConversationStore as CEConversationStore

    rt.extensions["chat"] = ChatService(rt, CEConversationStore())
    # CE-12: multi-model strategies over the same governed pipeline
    from chat_engine.orchestration import Orchestrator

    rt.extensions["orchestrator"] = Orchestrator(rt, rt.extensions["chat"])
    # CE-19: assignable skills over the upstream SkillRegistry
    from chat_engine.skills import SkillAssignmentService

    rt.extensions["skills"] = SkillAssignmentService(rt)
    # CE-15: usage + pricing (models.dev snapshot) + redacted logging / optional OTel
    from chat_engine.observability.logging import configure_logging
    from chat_engine.observability.otel import build_tracer
    from chat_engine.usage import PricingBook, UsageRecorder
    from chat_engine.usage.pricing import LITELLM_SNAPSHOT as _LITELLM_SNAPSHOT
    from chat_engine.usage.pricing import SNAPSHOT as _PRICING_SNAPSHOT

    pricing = PricingBook(
        lazy_snapshot=_PRICING_SNAPSHOT if env.get("CE_PRICING_SNAPSHOT", "1") != "0" else None,
        # CE-24/A: LiteLLM community table as a secondary (gap-filling) source
        lazy_litellm=_LITELLM_SNAPSHOT if env.get("CE_PRICING_LITELLM", "1") != "0" else None,
    )
    rt.extensions["usage_recorder"] = UsageRecorder(
        pricing, enabled=env.get("CE_FEATURE_USAGE", "1") != "0"
    )
    rt.extensions["usage_recorder"].keys = rt.extensions.get("keys")  # CE-24/D spend
    # CE-24/E: built-in guardrails (process defaults; projects add their own specs)
    from chat_engine.guardrails import GuardrailEngine

    rt.extensions["guardrails"] = GuardrailEngine.from_env(env.get("CE_GUARDRAILS_JSON"))
    # CE-24/C: live latency/in-flight signals; reorder only when CE_ROUTING_LIVE_SIGNALS=1
    from chat_engine.routing import LiveSignals

    rt.extensions["live_signals"] = LiveSignals(
        enabled=env.get("CE_ROUTING_LIVE_SIGNALS", "0") == "1"
    )
    rt.extensions["usage_recorder"].live_signals = rt.extensions["live_signals"]
    rt.extensions["tracer"] = build_tracer(env)
    # CE-14: App Factory (artifacts only; approval gate)
    from pathlib import Path as _Path

    from chat_engine.app_factory import AppFactoryRunner, InMemoryArtifactStore, LocalArtifactStore

    art_dir = env.get("CE_ARTIFACT_DIR")
    rt.extensions["app_factory"] = AppFactoryRunner(
        rt, LocalArtifactStore(_Path(art_dir)) if art_dir else InMemoryArtifactStore()
    )
    rt.extensions["logger"] = configure_logging(env.get("CE_LOG_LEVEL", "INFO"))
    for key in rt.provider_keys:
        pid = rt.providers.get(key).provider.id
        meta = next((b.limits_metadata for b in rt.bindings.bindings_for_provider(pid)), None)
        rt.extensions["admission"].set_limits(f"provider:{key}", Limits.from_metadata(meta))
    # Real providers (keys present) are bound by chat_engine.providers.bootstrap
    # (CE-05) — imported lazily so this module stays importable without httpx
    # transports in tests.
    from chat_engine.providers.bootstrap import bind_real_providers_from_env

    bind_real_providers_from_env(rt, env)
    # Remote-gateway-backed providers (ADR-0008 data plane; upstream adapter reused).
    from chat_engine.providers.gateway import bind_gateway_providers_from_env

    gw = bind_gateway_providers_from_env(rt, env)
    rt.extensions["gateway_report"] = {
        "bound": list(gw.bound),
        "skipped": dict(gw.skipped),
        "embed_only": list(gw.embed_only),  # CE-24/H
    }
    return rt
