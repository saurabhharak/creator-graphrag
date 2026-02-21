"""Standardized error response schema and exception handlers.

All API errors follow the ErrorResponse envelope:
{
    "error": {
        "code": "MACHINE_READABLE_CODE",
        "message": "Human-readable description",
        "details": {...} | null,
        "trace_id": "uuid"
    }
}
"""
from __future__ import annotations

import uuid

import structlog
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = structlog.get_logger(__name__)


class ErrorDetail(BaseModel):
    """Inner error payload with a machine-readable code and trace context."""

    code: str
    message: str
    details: dict | None = None
    trace_id: str


class ErrorResponse(BaseModel):
    """Standard error envelope returned by all API error responses."""

    error: ErrorDetail


def _make_error_response(
    code: str,
    message: str,
    status_code: int,
    details: dict | None = None,
    trace_id: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details,
                "trace_id": trace_id or str(uuid.uuid4()),
            }
        },
    )


# ─── Application-specific exceptions ───────────────────────────────────────

class AppError(Exception):
    """Base application error. All domain errors inherit from this."""

    code: str = "INTERNAL_ERROR"
    message: str = "An unexpected error occurred"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, message: str | None = None, details: dict | None = None) -> None:
        self.message = message or self.message
        self.details = details
        super().__init__(self.message)


class NotFoundError(AppError):
    """Raised when a requested resource does not exist."""

    code = "NOT_FOUND"
    message = "Resource not found"
    status_code = status.HTTP_404_NOT_FOUND


class ForbiddenError(AppError):
    """Raised when the caller lacks permission to perform an action."""

    code = "FORBIDDEN"
    message = "You don't have permission to perform this action"
    status_code = status.HTTP_403_FORBIDDEN


class ConflictError(AppError):
    """Raised when a resource already exists and cannot be duplicated."""

    code = "CONFLICT"
    message = "Resource already exists"
    status_code = status.HTTP_409_CONFLICT


class ValidationError(AppError):
    """Raised when request data fails domain-level validation."""

    code = "VALIDATION_ERROR"
    message = "Request validation failed"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY


class RateLimitError(AppError):
    """Raised when a caller exceeds their allowed request rate."""

    code = "RATE_LIMIT_EXCEEDED"
    message = "Rate limit exceeded"
    status_code = status.HTTP_429_TOO_MANY_REQUESTS


class UploadNotVerifiedError(AppError):
    """Raised when ingestion is triggered before the upload is confirmed."""

    code = "UPLOAD_NOT_VERIFIED"
    message = "File upload has not been confirmed yet"
    status_code = status.HTTP_400_BAD_REQUEST


class JobConcurrencyError(AppError):
    """Raised when the user already has the maximum concurrent jobs running."""

    code = "TOO_MANY_JOBS"
    message = "Maximum concurrent ingestion jobs reached"
    status_code = status.HTTP_429_TOO_MANY_REQUESTS


class PromptInjectionError(AppError):
    """Raised when user input contains patterns resembling LLM prompt injection."""

    code = "PROMPT_INJECTION_DETECTED"
    message = "Input contains disallowed patterns"
    status_code = status.HTTP_400_BAD_REQUEST


class LLMOutputValidationError(AppError):
    """Raised when LLM output fails Pydantic schema validation after retries."""

    code = "LLM_OUTPUT_INVALID"
    message = "LLM returned an output that failed schema validation"
    status_code = status.HTTP_502_BAD_GATEWAY


# ─── Exception handler registration ────────────────────────────────────────

def register_exception_handlers(app: FastAPI) -> None:
    """Register all application exception handlers on the FastAPI app.

    Args:
        app: The FastAPI application instance to register handlers on.
    """
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return _make_error_response(
            code=exc.code,
            message=exc.message,
            status_code=exc.status_code,
            details=getattr(exc, "details", None),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        code_map = {
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            409: "CONFLICT",
            422: "VALIDATION_ERROR",
            429: "RATE_LIMIT_EXCEEDED",
            500: "INTERNAL_ERROR",
        }
        return _make_error_response(
            code=code_map.get(exc.status_code, "HTTP_ERROR"),
            message=str(exc.detail),
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _make_error_response(
            code="VALIDATION_ERROR",
            message="Request validation failed",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            details={"errors": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Never swallow unhandled exceptions silently (STANDARDS.md §3.4)
        logger.error("unhandled_exception", exc_type=type(exc).__name__, exc_info=True)
        return _make_error_response(
            code="INTERNAL_ERROR",
            message="An unexpected error occurred",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
