"""Capability evidence ledger (ADR-CE-0001 D6) — a SIDE registry, no contract change.

Execution rule: ``declared ∧ verified.passed ∧ enabled ∧ available_now``, else
``unsupported_capability``. Upstream's 14 manifest booleans are read as
``declared``; additional capabilities (streaming_text, tool_calling,
structured_output, json_mode, vision_input...) live only here.

Default policy (recorded): auto-enable every ``declared ∧ verified.passed``
capability; ``failed``/``unverified`` are isolated; ``stale`` (TTL exceeded)
asks for a re-probe but keeps serving (not a hard refusal).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from core.contracts.base import BoundedStr, ContractModel, JsonObject
from core.contracts.provider import ProviderCapabilities
from pydantic import Field

from chat_engine.util.time import utc_now

#: Upstream's 14 manifest booleans — read as "declared" when true.
MANIFEST_CAPABILITIES: tuple[str, ...] = tuple(ProviderCapabilities.model_fields)
#: Extra capabilities tracked ONLY in this ledger (never on the frozen contract).
EXTRA_CAPABILITIES: tuple[str, ...] = (
    "streaming_text",
    "tool_calling",
    "structured_output",
    "json_mode",
    "long_context_128k",
    "system_prompt",
    "multi_turn",
)
ALL_CAPABILITIES: tuple[str, ...] = (*MANIFEST_CAPABILITIES, *EXTRA_CAPABILITIES)
DEFAULT_TTL = timedelta(days=7)


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    PASSED = "passed"
    FAILED = "failed"
    STALE = "stale"


class Declared(ContractModel):
    value: bool
    source: BoundedStr  # "manifest" | "admin" | "discovery" | "catalog:models.dev"


class Verified(ContractModel):
    status: VerificationStatus = VerificationStatus.UNVERIFIED
    probe_id: BoundedStr | None = None
    at: datetime | None = None
    evidence_ref: BoundedStr | None = None
    detail: BoundedStr | None = None


class Enabled(ContractModel):
    by_admin: bool = False
    auto: bool = False
    policy_ref: BoundedStr | None = None
    scopes: list[BoundedStr] = Field(default_factory=list)

    @property
    def effective(self) -> bool:
        return self.by_admin or self.auto


class CapabilityLedgerEntry(ContractModel):
    provider_key: BoundedStr
    model_key: BoundedStr | None = None
    capability: BoundedStr
    declared: Declared
    verified: Verified = Field(default_factory=Verified)
    enabled: Enabled = Field(default_factory=Enabled)
    available_now: bool = True
    updated_at: datetime

    @property
    def executable(self) -> bool:
        return (
            self.declared.value
            and self.verified.status in (VerificationStatus.PASSED, VerificationStatus.STALE)
            and self.enabled.effective
            and self.available_now
        )

    def why_not(self) -> str | None:
        if not self.declared.value:
            return "not declared"
        if self.verified.status is VerificationStatus.UNVERIFIED:
            return "unverified (no passing probe)"
        if self.verified.status is VerificationStatus.FAILED:
            return "probe failed"
        if not self.enabled.effective:
            return "disabled"
        if not self.available_now:
            return "unavailable now"
        return None


class ProbeResult(ContractModel):
    probe_id: BoundedStr
    provider_key: BoundedStr
    model_key: BoundedStr | None
    capability: BoundedStr
    passed: bool
    detail: BoundedStr | None = None
    latency_ms: int | None = None
    at: datetime
    evidence: JsonObject = Field(default_factory=dict)


class LedgerListResponse(ContractModel):
    entries: list[CapabilityLedgerEntry]
    total: int


class GateDecision(ContractModel):
    allowed: bool
    capability: BoundedStr
    provider_key: BoundedStr
    model_key: BoundedStr | None = None
    reason: BoundedStr | None = None


#: A probe: (provider_key, model_key) → ProbeResult-ish (passed, detail, latency_ms, evidence)
Probe = Callable[[str, str | None], Awaitable[tuple[bool, str | None, int | None, JsonObject]]]


class CapabilityLedger:
    def __init__(self, *, ttl: timedelta = DEFAULT_TTL, auto_enable: bool = True) -> None:
        self._entries: dict[tuple[str, str | None, str], CapabilityLedgerEntry] = {}
        self._probes: dict[str, Probe] = {}
        self._results: list[ProbeResult] = []
        self._ttl = ttl
        self._auto_enable = auto_enable

    # -- declaration ---------------------------------------------------------------------

    def declare_from_manifest(
        self, provider_key: str, capabilities: ProviderCapabilities, *, model_key: str | None = None
    ) -> int:
        n = 0
        for name in MANIFEST_CAPABILITIES:
            value = bool(getattr(capabilities, name))
            self.declare(provider_key, name, value, source="manifest", model_key=model_key)
            n += 1
        # extras are "declared" only by an explicit source — manifest says nothing
        return n

    def declare(
        self,
        provider_key: str,
        capability: str,
        value: bool,
        *,
        source: str,
        model_key: str | None = None,
    ) -> CapabilityLedgerEntry:
        if capability not in ALL_CAPABILITIES:
            msg = f"unknown capability: {capability}"
            raise ValueError(msg)
        key = (provider_key, model_key, capability)
        existing = self._entries.get(key)
        if existing is None:
            entry = CapabilityLedgerEntry(
                provider_key=provider_key,
                model_key=model_key,
                capability=capability,
                declared=Declared(value=value, source=source),
                updated_at=utc_now(),
            )
        else:
            entry = existing.model_copy(
                update={"declared": Declared(value=value, source=source), "updated_at": utc_now()}
            )
        self._entries[key] = entry
        return entry

    def forget_provider(self, provider_key: str) -> int:
        keys = [k for k in self._entries if k[0] == provider_key]
        for k in keys:
            del self._entries[k]
        return len(keys)

    # -- probes --------------------------------------------------------------------------

    def register_probe(self, capability: str, probe: Probe) -> None:
        self._probes[capability] = probe

    def has_probe(self, capability: str) -> bool:
        return capability in self._probes

    async def probe(
        self,
        provider_key: str,
        capability: str,
        *,
        model_key: str | None = None,
        evidence_ref: str | None = None,
    ) -> ProbeResult:
        fn = self._probes.get(capability)
        if fn is None:
            msg = f"no probe registered for capability: {capability}"
            raise LookupError(msg)
        import time

        started = time.perf_counter()
        try:
            passed, detail, latency, evidence = await fn(provider_key, model_key)
        except Exception as exc:
            passed, detail, latency, evidence = (
                False,
                f"probe raised {type(exc).__name__}",
                None,
                {},
            )
        if latency is None:
            latency = int((time.perf_counter() - started) * 1000)
        result = ProbeResult(
            probe_id=str(uuid4()),
            provider_key=provider_key,
            model_key=model_key,
            capability=capability,
            passed=passed,
            detail=detail,
            latency_ms=latency,
            at=utc_now(),
            evidence=evidence,
        )
        self._results.append(result)
        key = (provider_key, model_key, capability)
        entry = self._entries.get(key)
        if entry is None:
            entry = self.declare(
                provider_key, capability, True, source="discovery", model_key=model_key
            )
        status = VerificationStatus.PASSED if passed else VerificationStatus.FAILED
        enabled = entry.enabled
        if passed and self._auto_enable and entry.declared.value:
            enabled = enabled.model_copy(update={"auto": True})
        if not passed:
            enabled = enabled.model_copy(update={"auto": False})
        self._entries[key] = entry.model_copy(
            update={
                "verified": Verified(
                    status=status,
                    probe_id=result.probe_id,
                    at=result.at,
                    evidence_ref=evidence_ref,
                    detail=detail,
                ),
                "enabled": enabled,
                "updated_at": utc_now(),
            }
        )
        return result

    def record_external_verification(
        self,
        provider_key: str,
        capability: str,
        *,
        passed: bool,
        detail: str | None,
        model_key: str | None = None,
        evidence_ref: str | None = None,
    ) -> CapabilityLedgerEntry:
        """Verification evidence produced elsewhere (e.g. the lifecycle probe in CE-05)."""
        key = (provider_key, model_key, capability)
        entry = self._entries.get(key) or self.declare(
            provider_key, capability, True, source="discovery", model_key=model_key
        )
        status = VerificationStatus.PASSED if passed else VerificationStatus.FAILED
        enabled = entry.enabled.model_copy(
            update={"auto": bool(passed and self._auto_enable and entry.declared.value)}
        )
        updated = entry.model_copy(
            update={
                "verified": Verified(
                    status=status,
                    probe_id=str(uuid4()),
                    at=utc_now(),
                    evidence_ref=evidence_ref,
                    detail=detail,
                ),
                "enabled": enabled,
                "updated_at": utc_now(),
            }
        )
        self._entries[key] = updated
        return updated

    # -- admin ---------------------------------------------------------------------------

    def set_enabled(
        self,
        provider_key: str,
        capability: str,
        enabled: bool,
        *,
        model_key: str | None = None,
        policy_ref: str | None = None,
    ) -> CapabilityLedgerEntry:
        key = (provider_key, model_key, capability)
        entry = self._entries.get(key)
        if entry is None:
            raise LookupError(f"{provider_key}/{model_key}/{capability}")
        updated = entry.model_copy(
            update={
                "enabled": entry.enabled.model_copy(
                    update={
                        "by_admin": enabled,
                        "auto": entry.enabled.auto if enabled else False,
                        "policy_ref": policy_ref,
                    }
                ),
                "updated_at": utc_now(),
            }
        )
        self._entries[key] = updated
        return updated

    def set_available(self, provider_key: str, available: bool) -> None:
        for key, entry in list(self._entries.items()):
            if key[0] == provider_key and entry.available_now != available:
                self._entries[key] = entry.model_copy(update={"available_now": available})

    # -- reads ---------------------------------------------------------------------------

    def _refresh_stale(self, entry: CapabilityLedgerEntry) -> CapabilityLedgerEntry:
        v = entry.verified
        if (
            v.status is VerificationStatus.PASSED
            and v.at is not None
            and utc_now() - v.at > self._ttl
        ):
            return entry.model_copy(
                update={"verified": v.model_copy(update={"status": VerificationStatus.STALE})}
            )
        return entry

    def get(
        self, provider_key: str, capability: str, *, model_key: str | None = None
    ) -> CapabilityLedgerEntry | None:
        entry = self._entries.get((provider_key, model_key, capability)) or self._entries.get(
            (provider_key, None, capability)
        )
        return self._refresh_stale(entry) if entry else None

    def entries(
        self,
        *,
        provider_key: str | None = None,
        capability: str | None = None,
        executable_only: bool = False,
    ) -> list[CapabilityLedgerEntry]:
        rows = [self._refresh_stale(e) for e in self._entries.values()]
        if provider_key is not None:
            rows = [e for e in rows if e.provider_key == provider_key]
        if capability is not None:
            rows = [e for e in rows if e.capability == capability]
        if executable_only:
            rows = [e for e in rows if e.executable]
        return sorted(rows, key=lambda e: (e.provider_key, e.model_key or "", e.capability))

    def results(self, *, provider_key: str | None = None) -> list[ProbeResult]:
        return [r for r in self._results if provider_key is None or r.provider_key == provider_key]

    def gate(
        self, provider_key: str, capability: str, *, model_key: str | None = None
    ) -> GateDecision:
        entry = self.get(provider_key, capability, model_key=model_key)
        if entry is None:
            return GateDecision(
                allowed=False,
                capability=capability,
                provider_key=provider_key,
                model_key=model_key,
                reason="not declared",
            )
        return GateDecision(
            allowed=entry.executable,
            capability=capability,
            provider_key=provider_key,
            model_key=model_key,
            reason=entry.why_not(),
        )

    def providers_with(self, capability: str) -> list[str]:
        return sorted(
            {e.provider_key for e in self.entries(capability=capability, executable_only=True)}
        )

    def provider_ids_blocked_for(
        self,
        capability: str,
        *,
        provider_key_of: Callable[[UUID], str | None],
        provider_ids: list[UUID],
    ) -> list[UUID]:
        ok = set(self.providers_with(capability))
        return [pid for pid in provider_ids if (provider_key_of(pid) or "") not in ok]
