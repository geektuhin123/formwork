from __future__ import annotations


class FormworkError(Exception):
    """Base class; `code` is stable and safe to expose over the API."""

    code = "formwork_error"
    http_status = 500


class ValidationFailed(FormworkError):
    code = "validation_failed"
    http_status = 422


class NotFound(FormworkError):
    code = "not_found"
    http_status = 404


class Unauthorized(FormworkError):
    code = "unauthorized"
    http_status = 401


class Conflict(FormworkError):
    code = "conflict"
    http_status = 409


class UnsupportedFile(FormworkError):
    code = "unsupported_file"
    http_status = 415


class FileTooLarge(FormworkError):
    code = "file_too_large"
    http_status = 413


class BudgetExceeded(FormworkError):
    code = "budget_exceeded"
    http_status = 402


class ProviderError(FormworkError):
    code = "provider_error"
    http_status = 502


class SchemaCompileError(FormworkError):
    code = "schema_compile_error"
    http_status = 422


class MappingPlanError(FormworkError):
    code = "mapping_plan_error"
    http_status = 422
