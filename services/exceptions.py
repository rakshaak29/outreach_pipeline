"""
services/exceptions.py
----------------------
Backward-compatibility re-export shim.

All exception classes have been moved to the top-level ``exceptions`` module
so that ``utils/`` can import them without creating a utils → services
circular dependency.

Import from here OR directly from ``exceptions`` — both work identically.
"""

from exceptions import (  # noqa: F401  (re-exports)
    APIError,
    AuthenticationError,
    PipelineError,
    RateLimitError,
    StorageError,
    ValidationError,
)

__all__ = [
    "PipelineError",
    "ValidationError",
    "APIError",
    "AuthenticationError",
    "RateLimitError",
    "StorageError",
]
