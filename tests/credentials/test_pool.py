"""CE-24/K: credential rotation pool — park + rotate on credential-indicting categories,
no-op for single credential, exhausted when every account is parked, gateway env syntax,
admin read endpoint, provider breaker not tripped by a single rotated key."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

from chat_engine.admission.port import rotate_on_failure
from chat_engine.composition.runtime import echo_manifest
from chat_engine.credentials.pool import CredentialPool
from chat_engine.providers.gateway import gateway_binding_from_env
from core.contracts.domain import AuthType, Provider, ProviderStatus
from core.contracts.provider import (
    ProviderError,
    ProviderErrorCategory,
    ProviderGenerateRequest,
    ProviderGenerateResponse,
)

from tests.orchestration.test_strategies import ScriptedAdapter, add_scripted


def test_pool_rotation_and_parking() -> None:
    refs: dict = {}
    pool = CredentialPool(refs)
    pid = uuid4()
    pool.register(pid, "p", ["r1", "r2", "r3"])
    assert refs[pid] == "r1" and pool.active_label("p") == "acct-1" and pool.size("p") == 3
    # provider-indicting categories never rotate
    assert pool.on_failure("p", "timeout") is None and refs[pid] == "r1"
    assert pool.on_failure("p", "invalid_credential") == "credential_rotated"
    assert refs[pid] == "r2"
    assert pool.on_failure("p", "rate_limited", retry_after_ms=50) == "credential_rotated"
    assert refs[pid] == "r3"
    assert pool.on_failure("p", "quota_exceeded") == "credentials_exhausted"  # r1/r2 parked
    d = pool.describe("p")
    assert d["rotations"] == 2 and sum(1 for a in d["accounts"] if not a["available"]) == 3
    # a parked account becomes available again after its window
    acct2 = pool._pools[pid].accounts[1]
    acct2.parked_until = datetime.now(UTC) - timedelta(seconds=1)
    pool.unpark_expired()
    assert pool.describe("p")["accounts"][1]["available"] is True
    # single credential ⇒ no pool behaviour
    pid2 = uuid4()
    pool.register(pid2, "single", ["only"])
    assert pool.on_failure("single", "invalid_credential") is None
    # unknown provider
    assert pool.on_failure("nope", "invalid_credential") is None
    assert pool.describe("nope")["accounts"] == []
    # adopt + add on an old-style provider
    pid3 = uuid4()
    refs[pid3] = "legacy"
    pool.add(pid3, "legacy_p", "extra", label="backup")
    assert [a["label"] for a in pool.describe("legacy_p")["accounts"]] == ["acct-1", "backup"]


def test_gateway_env_accepts_multiple_tokens() -> None:
    b = gateway_binding_from_env(
        {
            "CE_GATEWAY_BASE_URL": "http://127.0.0.1:8800",
            "CE_GATEWAY_SECRET": "s",
            "CE_GATEWAY_SECRET_VERSION": "1",
            "CE_GATEWAY_ROUTES": "gw_a=t1|t2|t3,gw_b=t9",
        }
    )
    assert b is not None and b.routes["gw_a"] == "t1|t2|t3" and b.routes["gw_b"] == "t9"


class KeyFlaky(ScriptedAdapter):
    """Fails with invalid_credential while the ACTIVE ref is the bad one."""

    def __init__(self, manifest, bad_ref: str):
        super().__init__(manifest, {})
        self.bad_ref = bad_ref
        self.seen: list[str] = []

    async def generate(self, request: ProviderGenerateRequest) -> ProviderGenerateResponse:
        self.seen.append(request.credential_ref)
        if request.credential_ref == self.bad_ref:
            return ProviderGenerateResponse(
                request_id=request.request_id,
                succeeded=False,
                output={},
                usage={},
                latency_ms=1,
                error=ProviderError(
                    category=ProviderErrorCategory.INVALID_CREDENTIAL,
                    retryable=False,
                    safe_message="bad key",
                ),
            )
        return ProviderGenerateResponse(
            request_id=request.request_id,
            succeeded=True,
            output={"content": "fine"},
            usage={"total_tokens": 5},
            latency_ms=1,
        )


def test_end_to_end_rotation_recovers_and_breaker_untouched(client, runtime) -> None:
    manifest = echo_manifest().model_copy(update={"id": "rot", "name": "rot"})
    bad_ref = runtime.secrets.store(runtime.tenant_id, "BAD")
    good_ref = runtime.secrets.store(runtime.tenant_id, "GOOD")
    adapter = KeyFlaky(manifest, bad_ref)
    pid = uuid4()
    runtime.register_provider(
        provider=Provider(
            id=pid,
            provider_key="rot",
            display_name="rot",
            status=ProviderStatus.ACTIVE,
            auth_types=[AuthType.API_KEY],
            supports_account_pool=True,
        ),
        manifest=manifest,
        adapter=adapter,
        credential_ref=bad_ref,
        model_names=["rot-m"],
    )
    runtime.extensions["capabilities"].record_external_verification(
        "rot", "chat", passed=True, detail="t"
    )
    runtime.extensions["credential_pool"].register(pid, "rot", [bad_ref, good_ref])
    r1 = client.post("/v1/chat", json={"message": "a", "model": "rot-m"})
    assert r1.status_code == 502, r1.text
    assert r1.json()["error"]["details"]["provider_error_category"] == "invalid_credential"
    # the pool rotated: the next call uses the good key and succeeds
    r2 = client.post("/v1/chat", json={"message": "b", "model": "rot-m"})
    assert r2.status_code == 200, r2.text
    assert adapter.seen == [bad_ref, good_ref]
    creds = client.get("/v1/admin/providers/rot/credentials").json()
    assert creds["active"] == "acct-2" and creds["rotations"] == 1
    assert creds["accounts"][0]["available"] is False
    assert creds["accounts"][0]["last_error"] == "invalid_credential"
    assert "BAD" not in r1.text + r2.text + str(creds)  # secrets never leak
    snap = {s["scope"]: s for s in runtime.extensions["admission"].snapshot()}
    assert not snap["provider:rot"]["breaker_open"]
    assert client.get("/v1/admin/providers/nope/credentials").status_code == 404


def test_rotate_on_failure_helper_passthrough(runtime) -> None:
    err = NS(category=NS(value="timeout"), provider_code=None, retry_after_ms=None)
    rep = NS(nodes=[NS(response=NS(error=err), node=None, attempts=[])])
    assert rotate_on_failure(runtime, "unknown-provider", rep) == "timeout"
    assert rotate_on_failure(None, "x", rep) == "timeout"
    add_scripted(runtime, "plain", ["pm"], {"pm": "x"})
    assert rotate_on_failure(runtime, "plain", NS(nodes=[])) is None
