"""Unified error envelope — upstream ``ErrorCode`` (11) verbatim + chat_engine's
additive codes (docs/COMPATIBILITY_MAP.md §5). The additive codes NEVER appear
on ``/v1/execute``; ``downgrade_for_execute`` maps them to the upstream code.
"""

from __future__ import annotations

from enum import StrEnum

from core.contracts.base import JsonObject
from core.contracts.errors import ErrorCode, ErrorDetail, ErrorEnvelope
from core.contracts.provider import ProviderError, ProviderErrorCategory
from fastapi.responses import JSONResponse

#: HTTP status per upstream code (identical to apps/api/errors.py — a contract
#: fact re-stated, not imported, because apps.* is forbidden by D0).
HTTP_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.UNAUTHORIZED: 403,
    ErrorCode.ENTITLEMENT_EXCEEDED: 403,
    ErrorCode.CAPABILITY_DENIED: 403,
    ErrorCode.PROVIDER_UNAVAILABLE: 503,
    ErrorCode.MODEL_UNAVAILABLE: 503,
    ErrorCode.TOOL_APPROVAL_REQUIRED: 409,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.EXECUTION_FAILED: 502,
    ErrorCode.INTERNAL_ERROR: 500,
}


class ChatEngineErrorCode(StrEnum):
    """Additive codes — served only on /v1/chat/*, /v1/catalog/*, /v1/admin/providers/*."""

    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    ADMISSION_DENIED = "admission_denied"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    STREAM_INTERRUPTED = "stream_interrupted"
    ALIAS_UNCONFIRMED = "alias_unconfirmed"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"


HTTP_STATUS_BY_CE_CODE: dict[ChatEngineErrorCode, int] = {
    ChatEngineErrorCode.UNSUPPORTED_CAPABILITY: 422,
    ChatEngineErrorCode.ADMISSION_DENIED: 429,
    ChatEngineErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ChatEngineErrorCode.STREAM_INTERRUPTED: 502,
    ChatEngineErrorCode.ALIAS_UNCONFIRMED: 422,
    ChatEngineErrorCode.NOT_FOUND: 404,
    ChatEngineErrorCode.CONFLICT: 409,
}

DOWNGRADE_FOR_EXECUTE: dict[ChatEngineErrorCode, ErrorCode] = {
    ChatEngineErrorCode.UNSUPPORTED_CAPABILITY: ErrorCode.VALIDATION_ERROR,
    ChatEngineErrorCode.ADMISSION_DENIED: ErrorCode.RATE_LIMITED,
    ChatEngineErrorCode.IDEMPOTENCY_CONFLICT: ErrorCode.VALIDATION_ERROR,
    ChatEngineErrorCode.STREAM_INTERRUPTED: ErrorCode.EXECUTION_FAILED,
    ChatEngineErrorCode.ALIAS_UNCONFIRMED: ErrorCode.VALIDATION_ERROR,
    ChatEngineErrorCode.NOT_FOUND: ErrorCode.VALIDATION_ERROR,
    ChatEngineErrorCode.CONFLICT: ErrorCode.VALIDATION_ERROR,
}

_CODE_BY_PROVIDER_CATEGORY: dict[ProviderErrorCategory, ErrorCode] = {
    ProviderErrorCategory.RATE_LIMITED: ErrorCode.RATE_LIMITED,
    ProviderErrorCategory.QUOTA_EXCEEDED: ErrorCode.ENTITLEMENT_EXCEEDED,
    ProviderErrorCategory.MODEL_UNAVAILABLE: ErrorCode.MODEL_UNAVAILABLE,
    ProviderErrorCategory.PROVIDER_UNAVAILABLE: ErrorCode.PROVIDER_UNAVAILABLE,
}


def error_response(
    code: ErrorCode,
    message: str,
    *,
    retryable: bool = False,
    details: JsonObject | None = None,
    http_status: int | None = None,
) -> JSONResponse:
    """Upstream-shaped error body (byte-compatible with apps/api/errors.py)."""
    envelope = ErrorEnvelope(
        error=ErrorDetail(
            code=code, message=message, retryable=retryable, details=details or {}
        )
    )
    return JSONResponse(
        status_code=http_status if http_status is not None else HTTP_STATUS_BY_CODE[code],
        content=envelope.model_dump(mode="json"),
    )


def ce_error_response(
    code: ChatEngineErrorCode,
    message: str,
    *,
    retryable: bool = False,
    details: JsonObject | None = None,
    http_status: int | None = None,
) -> JSONResponse:
    """Same envelope shape, chat_engine code. Only on NEW routes."""
    body = {
        "error": {
            "code": code.value,
            "message": message,
            "retryable": retryable,
            "details": details or {},
            "trace_id": None,
        }
    }
    return JSONResponse(
        status_code=http_status if http_status is not None else HTTP_STATUS_BY_CE_CODE[code],
        content=body,
    )


def execution_failure_detail(
    execution_id: str,
    provider_error: ProviderError | None,
    *,
    stage: str | None = None,
) -> ErrorDetail:
    """Upstream mapping of a failed execution's last normalized error."""
    if provider_error is None:
        details: JsonObject = {"execution_id": execution_id}
        if stage is not None:
            details["stage"] = stage
        return ErrorDetail(
            code=ErrorCode.EXECUTION_FAILED,
            message="Execution failed.",
            retryable=False,
            details=details,
        )
    code = _CODE_BY_PROVIDER_CATEGORY.get(provider_error.category, ErrorCode.EXECUTION_FAILED)
    mapped: JsonObject = {
        "execution_id": execution_id,
        "provider_error_category": provider_error.category.value,
    }
    if stage is not None:
        mapped["stage"] = stage
    return ErrorDetail(
        code=code,
        message=provider_error.safe_message,
        retryable=provider_error.retryable,
        details=mapped,
    )
