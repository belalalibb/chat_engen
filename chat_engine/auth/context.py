"""Request-scoped principal (CE-24/D) without touching service signatures.

The auth middleware sets ``CURRENT_PRINCIPAL`` before dispatching; services call
``extra_scopes()`` (admission scope ``key:<id>``) and ``enforce_model()`` where they already
reserve admission. Anonymous / auth-off requests see no change (empty scopes, no checks).
"""

from __future__ import annotations

from contextvars import ContextVar
from uuid import UUID

from chat_engine.auth.contracts import Principal

CURRENT_PRINCIPAL: ContextVar[Principal | None] = ContextVar("ce_principal", default=None)


def current_principal() -> Principal | None:
    return CURRENT_PRINCIPAL.get()


def current_key_id() -> UUID | None:
    p = CURRENT_PRINCIPAL.get()
    return p.key_id if p is not None and not p.anonymous else None


def extra_scopes() -> list[str]:
    """Admission scopes contributed by the caller's key (``[]`` when anonymous)."""
    kid = current_key_id()
    return [f"key:{kid}"] if kid is not None else []


__all__ = ["CURRENT_PRINCIPAL", "current_key_id", "current_principal", "extra_scopes"]
