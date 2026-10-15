"""ADR-CE-0002: every upstream ErrorCode has an HTTP status; every chat_engine code has a
status, a downgrade for /v1/execute, and is documented in the ADR + docs/API.md."""

from __future__ import annotations

import re
from pathlib import Path

from chat_engine.api.errors import (
    DOWNGRADE_FOR_EXECUTE,
    HTTP_STATUS_BY_CE_CODE,
    HTTP_STATUS_BY_CODE,
    ChatEngineErrorCode,
    ce_error_response,
    error_response,
)
from core.contracts.errors import ErrorCode

ROOT = Path(__file__).resolve().parents[2]


def test_every_upstream_code_has_status_and_envelope() -> None:
    assert set(HTTP_STATUS_BY_CODE) == set(ErrorCode)
    for code in ErrorCode:
        r = error_response(code, "m")
        assert r.status_code == HTTP_STATUS_BY_CODE[code]
        body = r.body.decode()
        assert f'"code":"{code.value}"' in body and '"trace_id":null' in body


def test_every_ce_code_has_status_downgrade_and_docs() -> None:
    assert set(HTTP_STATUS_BY_CE_CODE) == set(ChatEngineErrorCode)
    assert set(DOWNGRADE_FOR_EXECUTE) == set(ChatEngineErrorCode)
    adr = (ROOT / "docs/adr/ADR-CE-0002-error-codes.md").read_text(encoding="utf-8")
    api = (ROOT / "docs/API.md").read_text(encoding="utf-8")
    for code in ChatEngineErrorCode:
        assert f"`{code.value}`" in adr, f"{code.value} missing from ADR-CE-0002"
        assert code.value in api, f"{code.value} missing from docs/API.md"
        r = ce_error_response(code, "m")
        assert r.status_code == HTTP_STATUS_BY_CE_CODE[code]
        # same envelope keys as upstream
        assert set(re.findall(r'"(\w+)":', r.body.decode())) >= {
            "code",
            "message",
            "retryable",
            "details",
            "trace_id",
        }


def test_downgrade_targets_are_upstream_codes() -> None:
    assert all(isinstance(v, ErrorCode) for v in DOWNGRADE_FOR_EXECUTE.values())
    assert DOWNGRADE_FOR_EXECUTE[ChatEngineErrorCode.ADMISSION_DENIED] is ErrorCode.RATE_LIMITED
