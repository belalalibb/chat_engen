"""Bind REAL upstream providers from environment keys (20 §5 flow).

Reuses upstream's proven adapters (``providers.real.groq``,
``providers.real.genspark_llm``) — no copy. Keys are stored once in the
runtime's ``InMemorySecretManager`` and adapters receive an opaque
``credential_ref`` resolved at the last moment. Nothing here logs a key.

OpenAI-compatible providers configured through env (``CE_OPENAI_COMPAT_*``) are
bound by CE-05's lifecycle service; this module only handles the two upstream
real providers so CE-02 stays small.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING
from uuid import uuid4

from core.contracts.domain import AuthType, Provider, ProviderStatus
from core.contracts.provider import ProviderManifest
from core.providers.ports import ProviderAdapterPort

if TYPE_CHECKING:
    from chat_engine.composition.runtime import Runtime

ENV_GROQ_KEY = "GROQ_API_KEY"
ENV_GSK_KEY = "GSK_API_KEY"


def _specs(
    env: Mapping[str, str],
) -> list[tuple[str, str, ProviderManifest, tuple[str, ...], Callable[..., ProviderAdapterPort]]]:
    specs: list[
        tuple[str, str, ProviderManifest, tuple[str, ...], Callable[..., ProviderAdapterPort]]
    ] = []
    groq_key = env.get(ENV_GROQ_KEY, "").strip()
    if groq_key:
        from providers.real.groq import MANIFEST as GROQ_MANIFEST
        from providers.real.groq import VERIFIED_TEXT_MODELS as GROQ_MODELS
        from providers.real.groq import GroqAdapter

        specs.append(("groq", groq_key, GROQ_MANIFEST, GROQ_MODELS, GroqAdapter))
    gsk_key = env.get(ENV_GSK_KEY, "").strip()
    if gsk_key:
        from providers.real.genspark_llm import MANIFEST as GENSPARK_MANIFEST
        from providers.real.genspark_llm import VERIFIED_TEXT_MODELS as GENSPARK_MODELS
        from providers.real.genspark_llm import GensparkLLMAdapter

        specs.append(
            ("genspark_llm", gsk_key, GENSPARK_MANIFEST, GENSPARK_MODELS, GensparkLLMAdapter)
        )
    return specs


def bind_real_providers_from_env(rt: Runtime, env: Mapping[str, str]) -> list[str]:
    bound: list[str] = []
    tenant = rt.tenant_id

    def _resolver(credential_ref: str) -> str:
        return str(rt.secrets.resolve(tenant, credential_ref))

    for provider_key, key, manifest, model_names, factory in _specs(env):
        ref = rt.secrets.store(tenant, key)
        adapter = factory(manifest, secret_resolver=_resolver, health_credential_ref=ref)
        provider = Provider(
            id=uuid4(),
            provider_key=provider_key,
            display_name=manifest.name,
            status=ProviderStatus.ACTIVE,
            auth_types=[AuthType.API_KEY],
            supports_account_pool=False,
        )
        rt.register_provider(
            provider=provider,
            manifest=manifest,
            adapter=adapter,
            credential_ref=ref,
            model_names=list(model_names),
        )
        bound.append(provider_key)
    return bound
