"""OpenAICompatibleAdapter — a full ``ProviderAdapterPort`` for any provider that
speaks the OpenAI chat-completions dialect (OpenAI, Groq, Together, OpenRouter,
vLLM, Ollama/OpenAI mode, LM Studio, local gateways...).

Discipline inherited from upstream's Groq adapter (pattern, not copy):
- the adapter NEVER sees where secrets live — ``secret_resolver(credential_ref)``
  is called at the last moment and the key never appears in any log/error;
- transport is injectable (``httpx.MockTransport`` in tests — zero network);
- every failure normalizes to the 12 upstream ``ProviderErrorCategory`` values
  with a ``safe_message`` (raw provider text never crosses the boundary).

Payload mapping (CE-03): prefers ``payload["messages"]`` (HandoffCompiler
output); falls back to the legacy ``role/context/previous_output/ask`` keys so
the adapter works for plain ``/v1/execute`` callers too.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from core.contracts.base import JsonObject
from core.contracts.domain import AuthType, CredentialStatus
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
    ProviderOperation,
)

from chat_engine.providers.error_details import (
    CONTEXT_WINDOW_CODE,
    context_window_facts,
    is_context_window_message,
)
from chat_engine.streaming.contracts import StreamChunk

DEFAULT_MAX_TOKENS = 2048
GENERATION_WHITELIST = frozenset(
    {
        "temperature",
        "top_p",
        "max_tokens",
        "max_completion_tokens",
        "stop",
        "seed",
        "presence_penalty",
        "frequency_penalty",
        "n",
    }
)


def build_manifest(
    *,
    provider_key: str,
    display_name: str,
    base_url: str,
    static_models: list[str] | None = None,
    discovery: str = "dynamic",
    capabilities: ProviderCapabilities | None = None,
    version: str = "1.0.0",
) -> ProviderManifest:
    """A REAL manifest for an OpenAI-compatible endpoint (never a template)."""
    return ProviderManifest.model_validate(
        {
            "id": provider_key,
            "name": display_name,
            "version": version,
            "status": "active",
            "auth": {"types": [AuthType.API_KEY.value], "supports_refresh": False},
            "account_pool": {"supported": False},
            "capabilities": (
                capabilities or ProviderCapabilities(chat=True, tool_use=True)
            ).model_dump(),
            "operations": [ProviderOperation.GENERATE_TEXT.value],
            "models": {"discovery": discovery, "static_models": list(static_models or [])},
            "rate_limits": {
                "strategy": "provider_defined",
                "dimensions": ["requests_per_minute", "tokens_per_minute"],
            },
            "health": {"checks": ["GET /models"]},
            "errors": {"mapping": "chat_engine.providers.openai_compatible"},
            "notes": [f"base_url={base_url}", "OpenAI chat-completions dialect"],
        }
    )


def messages_from_payload(payload: JsonObject) -> list[dict[str, Any]]:
    """CE-03 first; legacy keys second. Always ends with the user ask."""
    messages = payload.get("messages")
    if isinstance(messages, list) and messages:
        return [m for m in messages if isinstance(m, dict)]
    out: list[dict[str, Any]] = []
    role = payload.get("role")
    if isinstance(role, dict) and isinstance(role.get("objective"), str):
        out.append({"role": "system", "content": role["objective"]})
    context = payload.get("context")
    if isinstance(context, dict) and context:
        out.append(
            {"role": "system", "content": "Context:\n" + json.dumps(context, sort_keys=True)}
        )
    previous = payload.get("previous_output")
    if isinstance(previous, dict) and isinstance(previous.get("content"), str):
        out.append({"role": "assistant", "content": previous["content"]})
    ask = payload.get("ask")
    out.append({"role": "user", "content": ask if isinstance(ask, str) else ""})
    return out


def chat_body(request: ProviderGenerateRequest, *, stream: bool = False) -> dict[str, Any]:
    payload = request.payload
    body: dict[str, Any] = {
        "model": str(request.provider_model_name),
        "messages": messages_from_payload(payload),
        "stream": stream,
    }
    generation = payload.get("generation")
    if isinstance(generation, dict):
        for k, v in generation.items():
            if k in GENERATION_WHITELIST:
                body[k] = v
    body.setdefault("max_tokens", DEFAULT_MAX_TOKENS)
    tools = payload.get("tools")
    if isinstance(tools, list) and tools:
        body["tools"] = tools
        choice = payload.get("tool_choice")
        if choice is not None:
            body["tool_choice"] = choice
    schema = payload.get("response_schema")
    if (
        isinstance(schema, dict)
        and isinstance(schema.get("schema"), dict)
        and isinstance(schema.get("name"), str)
    ):
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema["name"], "schema": schema["schema"]},
        }
    elif payload.get("json_mode") is True:
        body["response_format"] = {"type": "json_object"}
    if stream:
        body["stream_options"] = {"include_usage": True}
    return body


def extract_content(parsed: dict[str, Any]) -> tuple[str, str | None, list[dict[str, Any]]]:
    choice = parsed["choices"][0]
    message = choice.get("message", {})
    content = message.get("content")
    if isinstance(content, list):  # some servers return parts
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    if not isinstance(content, str):
        content = ""
    finish = choice.get("finish_reason")
    tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
    return content, finish if isinstance(finish, str) else None, tool_calls


def extract_usage(parsed: dict[str, Any]) -> dict[str, Any]:
    usage = parsed.get("usage")
    if not isinstance(usage, dict):
        return {}
    return {
        k: usage[k]
        for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        if isinstance(usage.get(k), int)
    }


def retry_after_ms(response: httpx.Response) -> int | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0, int(float(raw) * 1000))
    except ValueError:
        return None


def _safe_code(response: httpx.Response) -> str | None:
    try:
        err = response.json().get("error")
    except ValueError:
        return None
    if isinstance(err, dict):
        code = err.get("code") or err.get("type")
        return str(code)[:64] if isinstance(code, str | int) else None
    return None


def _error_message(response: httpx.Response) -> str | None:
    """Raw provider message — used ONLY for classification (never echoed verbatim)."""
    try:
        body = response.json()
    except ValueError:
        return None
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        msg = err.get("message")
        return str(msg)[:512] if isinstance(msg, str) else None
    if isinstance(err, str):
        return err[:512]
    msg = body.get("message") if isinstance(body, dict) else None
    return str(msg)[:512] if isinstance(msg, str) else None


class OpenAICompatibleAdapter:
    def __init__(
        self,
        manifest: ProviderManifest,
        *,
        base_url: str,
        secret_resolver: Callable[[str], str],
        health_credential_ref: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        default_timeout_seconds: float = 60.0,
        extra_headers: dict[str, str] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        self._manifest = manifest
        self._base_url = base_url.rstrip("/")
        self._resolve = secret_resolver
        self._health_ref = health_credential_ref
        self._transport = transport
        self._timeout = default_timeout_seconds
        self._extra_headers = dict(extra_headers or {})
        self._clock = clock
        self._client: httpx.AsyncClient | None = None

    # -- plumbing ---------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        return self._base_url

    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                transport=self._transport,
                timeout=self._timeout,
                limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def headers(self, api_key: str) -> dict[str, str]:
        return {
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
            **self._extra_headers,
        }

    # -- ProviderAdapterPort ------------------------------------------------------------

    def get_manifest(self) -> ProviderManifest:
        return self._manifest

    async def get_capabilities(self) -> ProviderCapabilities:
        return self._manifest.capabilities

    async def validate_credential(self, credential_ref: str) -> CredentialHealth:
        api_key = self._resolve(credential_ref)
        try:
            r = await self.client().get("/models", headers=self.headers(api_key), timeout=15.0)
        except httpx.HTTPError:
            return CredentialHealth(
                credential_ref=credential_ref,
                status=CredentialStatus.ACTIVE,
                checked_at=self._clock(),
                detail="inconclusive: provider unreachable",
            )
        if r.status_code in (401, 403):
            return CredentialHealth(
                credential_ref=credential_ref,
                status=CredentialStatus.INVALID,
                checked_at=self._clock(),
                detail="provider rejected the credential",
            )
        if r.status_code == 200:
            return CredentialHealth(
                credential_ref=credential_ref,
                status=CredentialStatus.ACTIVE,
                checked_at=self._clock(),
            )
        return CredentialHealth(
            credential_ref=credential_ref,
            status=CredentialStatus.ACTIVE,
            checked_at=self._clock(),
            detail=f"inconclusive: http_{r.status_code}",
        )

    async def discover_models(self, account_id: UUID | None = None) -> list[DiscoveredModel]:
        if self._health_ref is None:
            return [
                DiscoveredModel(provider_model_name=m, modalities=["text"])
                for m in self._manifest.models.static_models
            ]
        api_key = self._resolve(self._health_ref)
        try:
            r = await self.client().get("/models", headers=self.headers(api_key), timeout=20.0)
        except httpx.HTTPError:
            return []
        if r.status_code != 200:
            return []
        try:
            data = r.json().get("data", [])
        except ValueError:
            return []
        out: list[DiscoveredModel] = []
        for row in data:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            limits: JsonObject = {}
            for key in ("context_window", "context_length", "max_model_len"):
                if isinstance(row.get(key), int):
                    limits["context_window"] = row[key]
                    break
            out.append(
                DiscoveredModel(
                    provider_model_name=row["id"],
                    modalities=["text"],
                    limits_metadata=limits,
                    capabilities={"chat": True},
                )
            )
        return out

    async def generate(self, request: ProviderGenerateRequest) -> ProviderGenerateResponse:
        if request.operation not in self._manifest.operations:
            return self._failed(
                request,
                ProviderError(
                    category=ProviderErrorCategory.UNSUPPORTED_CAPABILITY,
                    retryable=False,
                    safe_message=f"operation '{request.operation.value}' is not declared by this provider",
                ),
            )
        body = chat_body(request)
        timeout = request.timeout_ms / 1000.0 if request.timeout_ms is not None else self._timeout
        api_key = self._resolve(request.credential_ref)  # last moment (20 §5)
        started = time.monotonic()
        try:
            r = await self.client().post(
                "/chat/completions", headers=self.headers(api_key), json=body, timeout=timeout
            )
        except Exception as exc:
            return self._failed(request, self.normalize_error(exc))
        latency_ms = int((time.monotonic() - started) * 1000)
        if r.status_code != 200:
            return self._failed(request, self.normalize_error(r), latency_ms=latency_ms)
        try:
            parsed = r.json()
            content, finish, tool_calls = extract_content(parsed)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            return self._failed(request, self.normalize_error(exc), latency_ms=latency_ms)
        output: JsonObject = {"content": content, "finish_reason": finish}
        if tool_calls:
            output["tool_calls"] = tool_calls
        if isinstance(parsed.get("model"), str):
            output["model"] = parsed["model"]
        return ProviderGenerateResponse(
            request_id=request.request_id,
            succeeded=True,
            output=output,
            usage=extract_usage(parsed),
            latency_ms=latency_ms,
        )

    # -- StreamingAdapterPort (optional, CE-08) ------------------------------------------

    async def stream(self, request: ProviderGenerateRequest) -> AsyncIterator[StreamChunk]:
        """Token stream. Errors are yielded as chunks, never raised past here."""
        from chat_engine.streaming.sse_parser import iter_sse_chunks

        if request.operation not in self._manifest.operations:
            yield StreamChunk(
                error=ProviderError(
                    category=ProviderErrorCategory.UNSUPPORTED_CAPABILITY,
                    retryable=False,
                    safe_message="operation not declared by this provider",
                )
            )
            return
        body = chat_body(request, stream=True)
        timeout = request.timeout_ms / 1000.0 if request.timeout_ms is not None else self._timeout
        api_key = self._resolve(request.credential_ref)
        try:
            async with self.client().stream(
                "POST",
                "/chat/completions",
                headers=self.headers(api_key),
                json=body,
                timeout=timeout,
            ) as r:
                if r.status_code != 200:
                    await r.aread()
                    yield StreamChunk(error=self.normalize_error(r))
                    return
                async for chunk in iter_sse_chunks(r.aiter_lines()):
                    yield chunk
        except Exception as exc:
            yield StreamChunk(error=self.normalize_error(exc))

    async def health_check(self, scope: HealthScope) -> ProviderHealth:
        if self._health_ref is None:
            return ProviderHealth(
                provider_id=self._manifest.id,
                state=ProviderHealthState.DEGRADED,
                checked_at=self._clock(),
                detail="no health credential configured",
            )
        api_key = self._resolve(self._health_ref)
        try:
            r = await self.client().get("/models", headers=self.headers(api_key), timeout=10.0)
        except httpx.HTTPError:
            return ProviderHealth(
                provider_id=self._manifest.id,
                state=ProviderHealthState.UNAVAILABLE,
                checked_at=self._clock(),
                detail="unreachable",
            )
        if r.status_code == 200:
            return ProviderHealth(
                provider_id=self._manifest.id,
                state=ProviderHealthState.HEALTHY,
                checked_at=self._clock(),
            )
        if r.status_code in (401, 403):
            return ProviderHealth(
                provider_id=self._manifest.id,
                state=ProviderHealthState.SUSPENDED,
                checked_at=self._clock(),
                detail="credential rejected",
            )
        return ProviderHealth(
            provider_id=self._manifest.id,
            state=ProviderHealthState.DEGRADED,
            checked_at=self._clock(),
            detail=f"http_{r.status_code}",
        )

    def normalize_error(self, error: object) -> ProviderError:
        if isinstance(error, httpx.Response):
            return self._normalize_http(error)
        if isinstance(error, httpx.TimeoutException):
            return ProviderError(
                category=ProviderErrorCategory.TIMEOUT,
                retryable=True,
                provider_code="timeout",
                safe_message="provider request timed out",
            )
        if isinstance(error, httpx.HTTPError):
            return ProviderError(
                category=ProviderErrorCategory.PROVIDER_UNAVAILABLE,
                retryable=True,
                provider_code="transport",
                safe_message="provider unreachable",
            )
        return ProviderError(
            category=ProviderErrorCategory.NON_RETRYABLE_ERROR,
            retryable=False,
            provider_code=type(error).__name__[:64],
            safe_message="provider response could not be parsed",
        )

    def _normalize_http(self, r: httpx.Response) -> ProviderError:
        status = r.status_code
        code = _safe_code(r)
        if status in (401, 403):
            return ProviderError(
                category=ProviderErrorCategory.INVALID_CREDENTIAL,
                retryable=False,
                provider_code=code or f"http_{status}",
                safe_message="provider rejected the credential",
            )
        if status == 429:
            cat = (
                ProviderErrorCategory.QUOTA_EXCEEDED
                if code and ("quota" in code or "billing" in code or "insufficient" in code)
                else ProviderErrorCategory.RATE_LIMITED
            )
            return ProviderError(
                category=cat,
                retryable=cat is ProviderErrorCategory.RATE_LIMITED,
                retry_after_ms=retry_after_ms(r),
                provider_code=code or "http_429",
                safe_message="provider rate limit reached"
                if cat is ProviderErrorCategory.RATE_LIMITED
                else "provider quota exceeded",
            )
        if status == 404 or (status == 400 and code and "model" in code):
            return ProviderError(
                category=ProviderErrorCategory.MODEL_UNAVAILABLE,
                retryable=False,
                provider_code=code or f"http_{status}",
                safe_message="requested model is not available at the provider",
            )
        if status in (400, 413, 422):
            raw_msg = _error_message(r)
            if is_context_window_message(raw_msg, code):
                # CE-24/G: the REQUEST is too large for this model — request-indicting,
                # never retried or failed over; facts travel in the safe message so the
                # API layer can surface details.max_input_tokens / requested_tokens.
                facts = context_window_facts(raw_msg)
                parts = ["prompt exceeds the model's context window"]
                if "max_input_tokens" in facts:
                    parts.append(f"maximum {facts['max_input_tokens']} tokens")
                if "requested_tokens" in facts:
                    parts.append(f"requested {facts['requested_tokens']} tokens")
                return ProviderError(
                    category=ProviderErrorCategory.BAD_REQUEST,
                    retryable=False,
                    provider_code=CONTEXT_WINDOW_CODE,
                    safe_message="; ".join(parts),
                )
            if code and ("content" in code or "safety" in code or "moderation" in code):
                return ProviderError(
                    category=ProviderErrorCategory.CONTENT_REJECTED,
                    retryable=False,
                    provider_code=code,
                    safe_message="provider rejected the content",
                )
            if code and ("response_format" in code or "unsupported" in code or "tool" in code):
                return ProviderError(
                    category=ProviderErrorCategory.UNSUPPORTED_CAPABILITY,
                    retryable=False,
                    provider_code=code,
                    safe_message="provider does not support a requested feature",
                )
            return ProviderError(
                category=ProviderErrorCategory.BAD_REQUEST,
                retryable=False,
                provider_code=code or f"http_{status}",
                safe_message="provider rejected the request",
            )
        if status in (408, 504):
            return ProviderError(
                category=ProviderErrorCategory.TIMEOUT,
                retryable=True,
                provider_code=code or f"http_{status}",
                safe_message="provider request timed out",
            )
        if status in (500, 502, 503, 529):
            return ProviderError(
                category=ProviderErrorCategory.RETRYABLE_SERVER_ERROR
                if status != 503
                else ProviderErrorCategory.PROVIDER_UNAVAILABLE,
                retryable=True,
                retry_after_ms=retry_after_ms(r),
                provider_code=code or f"http_{status}",
                safe_message="provider temporarily unavailable",
            )
        return ProviderError(
            category=ProviderErrorCategory.NON_RETRYABLE_ERROR,
            retryable=False,
            provider_code=code or f"http_{status}",
            safe_message="provider returned an unexpected error",
        )

    @staticmethod
    def _failed(
        request: ProviderGenerateRequest, error: ProviderError, *, latency_ms: int | None = None
    ) -> ProviderGenerateResponse:
        return ProviderGenerateResponse(
            request_id=request.request_id,
            succeeded=False,
            output={},
            usage={},
            error=error,
            latency_ms=latency_ms,
        )
