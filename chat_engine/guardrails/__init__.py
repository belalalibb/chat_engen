"""Guardrails (CE-24/E) — litellm-style pre/post hooks, built-ins only (no custom code).

A ``Project.guardrails`` entry (or the process default ``CE_GUARDRAILS_JSON``) looks like::

    {"type": "keyword_block", "stage": "pre", "keywords": ["secret project"], "action": "block"}
    {"type": "pii_redact", "stage": "both", "kinds": ["email", "phone", "card"]}

``stage`` ∈ pre | post | both (default both). Verdicts: allow | redact | block. A block
on the request → 403 ``capability_denied`` with ``details.guardrail``; a block on the
response → the assistant turn is stored FAILED with the guardrail reason and the API
returns 403 too. Redactions rewrite the text and are recorded in ``turn.metadata.guardrails``.
"""

from chat_engine.guardrails.engine import GuardrailEngine, GuardrailVerdict, build_guardrails
from chat_engine.guardrails.port import GuardrailPort, Verdict

__all__ = [
    "GuardrailEngine",
    "GuardrailPort",
    "GuardrailVerdict",
    "Verdict",
    "build_guardrails",
]
