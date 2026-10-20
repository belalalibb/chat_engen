"""Virtual API keys + spend tracking (CE-24/D, litellm "virtual keys" pattern).

Opt-in: ``CE_AUTH_MODE=off`` (default) keeps every request anonymous exactly as before;
``optional`` resolves a Bearer key when present; ``required`` rejects requests without a
valid key on the protected prefixes (401 ``unauthenticated``).
"""

from chat_engine.auth.contracts import (
    KeyCreate,
    KeyCreated,
    KeyRecord,
    KeySpend,
    KeyUpdate,
    Principal,
)
from chat_engine.auth.service import AuthMode, KeyService

__all__ = [
    "AuthMode",
    "KeyCreate",
    "KeyCreated",
    "KeyRecord",
    "KeyService",
    "KeySpend",
    "KeyUpdate",
    "Principal",
]
