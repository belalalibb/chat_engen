"""Built-in guardrails (CE-24/E): ``keyword_block`` and ``pii_redact`` (regex)."""

from __future__ import annotations

import re

from core.contracts.base import JsonObject

from chat_engine.guardrails.port import GuardrailResult, Verdict

_PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    # international / local phone numbers with 9+ digits (spaces, dashes, dots allowed)
    "phone": re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)"),
    # 13-19 digit card numbers with optional separators (Luhn not enforced: redaction only)
    "card": re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}\b"),
    "ip": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}
DEFAULT_PII_KINDS = ("email", "phone", "card", "iban")


def _digits(s: str) -> int:
    return sum(ch.isdigit() for ch in s)


class KeywordBlock:
    """Blocks (or redacts) when any configured keyword/phrase appears (case-insensitive,
    whole-word for single tokens, substring for phrases)."""

    name = "keyword_block"

    def __init__(self, keywords: list[str], *, action: str = "block", mask: str = "[blocked]"):
        self.keywords = [k for k in (str(x).strip() for x in keywords) if k]
        self.action = Verdict.REDACT if action == "redact" else Verdict.BLOCK
        self.mask = mask
        self._patterns = [
            re.compile(rf"\b{re.escape(k)}\b" if " " not in k else re.escape(k), re.I)
            for k in self.keywords
        ]

    def _scan(self, text: str) -> GuardrailResult:
        hits = [k for k, p in zip(self.keywords, self._patterns, strict=True) if p.search(text)]
        if not hits:
            return GuardrailResult(Verdict.ALLOW, text)
        if self.action is Verdict.REDACT:
            out = text
            for p in self._patterns:
                out = p.sub(self.mask, out)
            return GuardrailResult(
                Verdict.REDACT, out, reason="keyword", details={"keywords": hits}
            )
        return GuardrailResult(Verdict.BLOCK, text, reason="keyword", details={"keywords": hits})

    def pre_request(self, text: str) -> GuardrailResult:
        return self._scan(text)

    def post_response(self, text: str) -> GuardrailResult:
        return self._scan(text)


class PiiRedact:
    """Redacts e-mails, phone numbers, card numbers, IBANs (and optionally IPs)."""

    name = "pii_redact"

    def __init__(self, kinds: list[str] | None = None, *, action: str = "redact"):
        wanted = [k for k in (kinds or list(DEFAULT_PII_KINDS)) if k in _PII_PATTERNS]
        self.kinds = wanted or list(DEFAULT_PII_KINDS)
        self.action = Verdict.BLOCK if action == "block" else Verdict.REDACT

    def _scan(self, text: str) -> GuardrailResult:
        counts: JsonObject = {}
        out = text
        for kind in self.kinds:
            pat = _PII_PATTERNS[kind]

            def repl(m: re.Match[str], kind: str = kind) -> str:
                if kind in ("phone", "card") and _digits(m.group(0)) < (
                    9 if kind == "phone" else 13
                ):
                    return m.group(0)
                counts[kind] = int(counts.get(kind, 0)) + 1
                return f"[{kind}]"

            out = pat.sub(repl, out)
        if not counts:
            return GuardrailResult(Verdict.ALLOW, text)
        if self.action is Verdict.BLOCK:
            return GuardrailResult(Verdict.BLOCK, text, reason="pii", details={"found": counts})
        return GuardrailResult(Verdict.REDACT, out, reason="pii", details={"redacted": counts})

    def pre_request(self, text: str) -> GuardrailResult:
        return self._scan(text)

    def post_response(self, text: str) -> GuardrailResult:
        return self._scan(text)


__all__ = ["DEFAULT_PII_KINDS", "KeywordBlock", "PiiRedact"]
