"""BreakerPolicy — per-category circuit-breaker rules (CE-24/B, litellm ``allowed_fails_policy``).

Default (env unset) is the legacy relative breaker: any failure counts, 5 failures in the
last 10 attempts open the provider scope for 30s. A policy refines that *per provider error
category*::

    CE_BREAKER_POLICY_JSON='{"timeout": 3, "provider_unavailable": 3, "rate_limited": 10,
                             "invalid_credential": {"fails": 1, "open_seconds": 600},
                             "quota_exceeded":     {"fails": 1, "open_seconds": 600},
                             "context_window": 0, "bad_request": 0}'

* ``int`` → that many failures of the category within the sample window trip the breaker
  (open for the legacy 30s);
* ``{"fails": n, "open_seconds": s}`` → both knobs;
* ``0`` → the category is the *request's* fault (context window, bad request): it never
  counts towards the breaker at all.

Categories without a rule keep the legacy relative breaker. Rules are only consulted for
provider scopes (tenant/project scopes carry quotas, never health).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

BREAKER_FAILS = 5  # failures in the last BREAKER_SAMPLE attempts
BREAKER_SAMPLE = 10
BREAKER_OPEN_S = 30.0
#: failures of one category are counted within this many seconds
CATEGORY_WINDOW_S = 60.0


@dataclass(frozen=True)
class CategoryRule:
    fails: int
    open_seconds: float = BREAKER_OPEN_S

    @property
    def counts(self) -> bool:
        return self.fails > 0


@dataclass(frozen=True)
class BreakerPolicy:
    rules: dict[str, CategoryRule] = field(default_factory=dict)
    fails: int = BREAKER_FAILS
    sample: int = BREAKER_SAMPLE
    open_seconds: float = BREAKER_OPEN_S

    @classmethod
    def legacy(cls) -> BreakerPolicy:
        return cls()

    @classmethod
    def recommended(cls) -> BreakerPolicy:
        """The policy documented in docs/OPERATIONS.md — opt-in via ``recommended``."""
        return cls.parse(
            '{"timeout": 3, "provider_unavailable": 3, "retryable_server_error": 3,'
            ' "rate_limited": 10,'
            ' "invalid_credential": {"fails": 1, "open_seconds": 600},'
            ' "auth_expired": {"fails": 1, "open_seconds": 600},'
            ' "quota_exceeded": {"fails": 1, "open_seconds": 600},'
            ' "context_window": 0, "bad_request": 0, "content_rejected": 0,'
            ' "unsupported_capability": 0}'
        )

    @classmethod
    def from_env(cls, raw: str | None) -> BreakerPolicy:
        text = (raw or "").strip()
        if not text:
            return cls.legacy()
        if text.lower() == "recommended":
            return cls.recommended()
        return cls.parse(text)

    @classmethod
    def parse(cls, text: str) -> BreakerPolicy:
        doc = json.loads(text)
        if not isinstance(doc, dict):
            raise ValueError("CE_BREAKER_POLICY_JSON must be a JSON object")
        rules: dict[str, CategoryRule] = {}
        fails, sample, open_s = BREAKER_FAILS, BREAKER_SAMPLE, BREAKER_OPEN_S
        for key, val in doc.items():
            if key == "_default":
                if isinstance(val, dict):
                    fails = int(val.get("fails", fails))
                    sample = int(val.get("sample", sample))
                    open_s = float(val.get("open_seconds", open_s))
                continue
            if isinstance(val, bool):
                raise ValueError(f"breaker rule for {key!r} must be int or object")
            if isinstance(val, int | float):
                rules[str(key)] = CategoryRule(fails=int(val), open_seconds=open_s)
            elif isinstance(val, dict):
                rules[str(key)] = CategoryRule(
                    fails=int(val.get("fails", 1)),
                    open_seconds=float(val.get("open_seconds", open_s)),
                )
            else:
                raise ValueError(f"breaker rule for {key!r} must be int or object")
        for k, r in rules.items():
            if r.fails < 0 or r.open_seconds < 0:
                raise ValueError(f"breaker rule for {k!r} must be non-negative")
        return cls(rules=rules, fails=fails, sample=sample, open_seconds=open_s)

    def rule_for(self, category: str | None) -> CategoryRule | None:
        if category is None:
            return None
        return self.rules.get(category)

    def describe(self) -> dict[str, object]:
        return {
            "default": {
                "fails": self.fails,
                "sample": self.sample,
                "open_seconds": self.open_seconds,
            },
            "categories": {
                k: {"fails": r.fails, "open_seconds": r.open_seconds} for k, r in self.rules.items()
            },
        }


__all__ = [
    "BREAKER_FAILS",
    "BREAKER_OPEN_S",
    "BREAKER_SAMPLE",
    "CATEGORY_WINDOW_S",
    "BreakerPolicy",
    "CategoryRule",
]
