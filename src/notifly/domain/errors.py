"""Domain error hierarchy.

Every error exposes an HTTP-friendly status code and a machine-readable code
so the presentation layer can map them to consistent API responses without
embedding business logic in routes.
"""

from __future__ import annotations

from typing import Any


class NotiFlyError(Exception):
    """Base class for all NotiFly errors."""

    code: str = "internal_error"
    status_code: int = 500

    def __init__(self, detail: str = "", *, payload: dict[str, Any] | None = None) -> None:
        self.detail = detail
        self.payload = payload or {}
        super().__init__(detail)


class InvalidDataError(NotiFlyError):
    code = "invalid_data"
    status_code = 400


class AuthenticationError(NotiFlyError):
    code = "unauthenticated"
    status_code = 401


class ForbiddenError(NotiFlyError):
    code = "forbidden"
    status_code = 403


class NotFoundError(NotiFlyError):
    code = "not_found"
    status_code = 404


class ConflictError(NotiFlyError):
    code = "conflict"
    status_code = 409


class AlreadyExistsError(ConflictError):
    code = "already_exists"


class IdempotencyConflictError(ConflictError):
    code = "idempotency_conflict"


class InvalidStateError(ConflictError):
    code = "invalid_state"


class RateLimitExceededError(NotiFlyError):
    code = "rate_limited"
    status_code = 429


class VariableValidationError(InvalidDataError):
    code = "variable_validation"


class TemplateRenderingError(InvalidDataError):
    code = "template_rendering"


class ProviderConfigurationError(InvalidDataError):
    code = "provider_configuration"


class ProviderNotRegisteredError(InvalidDataError):
    code = "provider_not_registered"


class ProviderError(NotiFlyError):
    """A provider invocation failed."""

    code = "provider_error"
    status_code = 502
