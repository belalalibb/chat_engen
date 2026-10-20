"""CE-24/H: POST /v1/embeddings over a provider declaring create_embeddings only.
Embed-only providers are invisible to generate_text routing; errors map through the
unified envelope; usage + pricing (litellm voyage-* rows) are recorded."""

from __future__ import annotations

from uuid import uuid4

from core.contracts.domain import AuthType, Provider, ProviderStatus
from core.contracts.provider import (
    ProviderError,
    ProviderErrorCategory,
    ProviderGenerateRequest,
    ProviderGenerateResponse,
    ProviderOperation,
)

from chat_engine.composition.runtime import LocalEchoAdapter, Runtime, echo_manifest


class EmbedAdapter(LocalEchoAdapter):
    def __init__(self, manifest, *, fail: bool = False):
        super().__init__(manifest)
        self.fail = fail
        self.payloads: list[dict] = []

    async def generate(self, request: ProviderGenerateRequest) -> ProviderGenerateResponse:
        self.payloads.append(dict(request.payload))
        if self.fail:
            return ProviderGenerateResponse(
                request_id=request.request_id,
                succeeded=False,
                output={},
                usage={},
                latency_ms=1,
                error=ProviderError(
                    category=ProviderErrorCategory.INVALID_CREDENTIAL,
                    retryable=False,
                    provider_code="no_key",
                    safe_message="no active API key available in pool",
                ),
            )
        inputs = request.payload["inputs"]
        return ProviderGenerateResponse(
            request_id=request.request_id,
            succeeded=True,
            output={
                "embeddings": [[round(0.1 * (i + 1), 1), 0.2, 0.3] for i in range(len(inputs))],
                "dimensions": 3,
            },
            usage={"input_tokens": 7 * len(inputs), "units": len(inputs)},
            latency_ms=3,
        )


def add_embed_provider(
    rt: Runtime, key: str, models: list[str], *, fail: bool = False
) -> EmbedAdapter:
    manifest = echo_manifest().model_copy(
        update={"id": key, "name": key, "operations": [ProviderOperation.CREATE_EMBEDDINGS]}
    )
    adapter = EmbedAdapter(manifest, fail=fail)
    pid = uuid4()
    rt.register_provider(
        provider=Provider(
            id=pid,
            provider_key=key,
            display_name=key,
            status=ProviderStatus.ACTIVE,
            auth_types=[AuthType.API_KEY],
            supports_account_pool=False,
        ),
        manifest=manifest,
        adapter=adapter,
        credential_ref=f"secret-ref://{key}",
        model_names=models,
    )
    rt.extensions.setdefault("embedding_routes", {})[key] = {
        "provider_id": str(pid),
        "models": models,
    }
    return adapter


def test_no_embedding_route_is_unsupported(client) -> None:
    r = client.post("/v1/embeddings", json={"model": "voyage-3", "input": "x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unsupported_capability"
    assert client.get("/v1/embeddings/models").json() == {"object": "list", "data": []}


def test_embeddings_roundtrip_usage_and_isolation(client, runtime: Runtime) -> None:
    ad = add_embed_provider(runtime, "emb", ["voyage-3-lite", "voyage-3"])
    models = client.get("/v1/embeddings/models").json()["data"]
    assert {m["id"] for m in models} == {"voyage-3-lite", "voyage-3"}
    r = client.post("/v1/embeddings", json={"model": "voyage-3-lite", "input": ["hello", "world"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "list" and len(body["data"]) == 2 and body["dimensions"] == 3
    assert body["data"][1] == {"object": "embedding", "index": 1, "embedding": [0.2, 0.2, 0.3]}
    assert body["provider_key"] == "emb" and body["usage"]["prompt_tokens"] == 14
    assert ad.payloads == [{"inputs": ["hello", "world"]}]  # gateway CONTRACT §5 shape
    # single string input
    assert client.post("/v1/embeddings", json={"model": "voyage-3", "input": "one"}).status_code == 200
    # usage recorded on its own surface with litellm pricing for voyage-*
    recs = client.get("/v1/usage/records", params={"limit": 100}).json()["records"]
    emb = [x for x in recs if x["surface"] == "embeddings"]
    assert len(emb) == 2 and emb[0]["tokens"]["input"] == 14
    assert emb[0]["cost"]["pricing_ref"] and emb[0]["cost"]["pricing_ref"].startswith("litellm:")
    # the embed-only provider never serves chat (operation gate upstream)
    chat = client.post("/v1/chat", json={"message": "hi", "model": "voyage-3-lite"})
    assert chat.status_code in (503, 422), chat.text
    # validation
    assert client.post("/v1/embeddings", json={"model": "voyage-3", "input": []}).status_code == 422
    assert client.post("/v1/embeddings", json={"model": "voyage-3", "input": [""]}).status_code == 422
    bad_fmt = {"model": "voyage-3", "input": "x", "encoding_format": "base64"}
    assert client.post("/v1/embeddings", json=bad_fmt).status_code == 422
    nf = client.post("/v1/embeddings", json={"model": "nope-embed", "input": "x"})
    assert nf.status_code == 503 and nf.json()["error"]["code"] == "model_unavailable"
    assert "voyage-3" in nf.json()["error"]["details"]["available"]


def test_embeddings_provider_error_maps_to_envelope(client, runtime: Runtime) -> None:
    add_embed_provider(runtime, "embad", ["bad-embed"], fail=True)
    r = client.post("/v1/embeddings", json={"model": "bad-embed", "input": "x"})
    assert r.status_code == 502, r.text
    err = r.json()["error"]
    assert err["code"] == "execution_failed"
    assert err["details"]["provider_error_category"] == "invalid_credential"
