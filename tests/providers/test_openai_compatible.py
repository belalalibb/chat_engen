"""CE-05: OpenAICompatibleAdapter contract behaviour against the fake server."""

from __future__ import annotations

from uuid import uuid4

import pytest
from chat_engine.providers.openai_compatible import OpenAICompatibleAdapter, build_manifest
from core.contracts.domain import CredentialStatus
from core.contracts.provider import (
    HealthScope,
    ProviderErrorCategory,
    ProviderGenerateRequest,
    ProviderOperation,
)

from tests.providers.fake_openai import FakeOpenAI

SECRETS = {"ref-good": "fakekey-valid-0001", "ref-bad": "fakekey-wrong-0000"}


def adapter(fake: FakeOpenAI, ref: str = "ref-good") -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        build_manifest(provider_key="fake", display_name="Fake", base_url="https://fake.local/v1"),
        base_url="https://fake.local/v1",
        secret_resolver=SECRETS.__getitem__,
        health_credential_ref=ref,
        transport=fake.transport(),
    )


def req(model: str = "fake-chat-1", **payload) -> ProviderGenerateRequest:
    return ProviderGenerateRequest(
        request_id=uuid4(),
        tenant_id=uuid4(),
        operation=ProviderOperation.GENERATE_TEXT,
        provider_model_name=model,
        credential_ref="ref-good",
        payload=payload or {"ask": "hi"},
    )


async def test_generate_success_and_secret_never_in_output() -> None:
    fake = FakeOpenAI()
    resp = await adapter(fake).generate(req())
    assert resp.succeeded and resp.output["content"] == "echo:hi"
    assert resp.usage == {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}
    assert fake.calls[0]["messages"] == [{"role": "user", "content": "hi"}]
    assert fake.calls[0]["stream"] is False
    assert "fakekey-valid" not in resp.model_dump_json()


async def test_messages_from_handoff_take_precedence_over_ask() -> None:
    fake = FakeOpenAI()
    msgs = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "q2"}]
    await adapter(fake).generate(req(ask="legacy", messages=msgs))
    assert fake.calls[0]["messages"] == msgs


async def test_validate_credential_good_and_bad() -> None:
    fake = FakeOpenAI()
    good = await adapter(fake).validate_credential("ref-good")
    bad = await adapter(fake, "ref-bad").validate_credential("ref-bad")
    assert good.status is CredentialStatus.ACTIVE
    assert bad.status is CredentialStatus.INVALID


async def test_discover_models_reads_models_endpoint() -> None:
    fake = FakeOpenAI(models=["a", "b", "c"])
    found = await adapter(fake).discover_models()
    assert [d.provider_model_name for d in found] == ["a", "b", "c"]
    assert found[0].limits_metadata == {"context_window": 32768}


@pytest.mark.parametrize(
    ("status", "retry_after", "category", "retryable"),
    [
        (429, "2", ProviderErrorCategory.RATE_LIMITED, True),
        (500, None, ProviderErrorCategory.RETRYABLE_SERVER_ERROR, True),
        (503, None, ProviderErrorCategory.PROVIDER_UNAVAILABLE, True),
        (400, None, ProviderErrorCategory.BAD_REQUEST, False),
    ],
)
async def test_http_errors_normalize(
    status: int, retry_after: str | None, category: ProviderErrorCategory, retryable: bool
) -> None:
    fake = FakeOpenAI(fail_next=[status], retry_after=retry_after)
    resp = await adapter(fake).generate(req())
    assert not resp.succeeded and resp.error is not None
    assert resp.error.category is category and resp.error.retryable is retryable
    if retry_after:
        assert resp.error.retry_after_ms == 2000
    assert "scripted" not in resp.error.safe_message  # raw text never crosses


async def test_unknown_model_is_model_unavailable() -> None:
    resp = await adapter(FakeOpenAI()).generate(req(model="nope"))
    assert resp.error is not None and resp.error.category is ProviderErrorCategory.MODEL_UNAVAILABLE


async def test_timeout_normalizes() -> None:
    resp = await adapter(FakeOpenAI(slow=True)).generate(req())
    assert resp.error is not None and resp.error.category is ProviderErrorCategory.TIMEOUT


async def test_wrong_key_is_invalid_credential() -> None:
    fake = FakeOpenAI()
    r = req()
    r = r.model_copy(update={"credential_ref": "ref-bad"})
    resp = await adapter(fake).generate(r)
    assert (
        resp.error is not None and resp.error.category is ProviderErrorCategory.INVALID_CREDENTIAL
    )


async def test_health_check() -> None:
    assert (await adapter(FakeOpenAI()).health_check(HealthScope.PROVIDER)).state.value == "HEALTHY"
    assert (
        await adapter(FakeOpenAI(), "ref-bad").health_check(HealthScope.PROVIDER)
    ).state.value == "SUSPENDED"
