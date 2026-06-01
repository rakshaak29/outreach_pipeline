"""
services/exceptions.py
----------------------
Project-wide custom exception hierarchy for the Automated Outreach Pipeline.

Keeping all exceptions in one place makes it trivial to catch any pipeline
error with a single ``except PipelineError`` clause at the top level.

Hierarchy:
    PipelineError
    ├── ValidationError       – bad input (domain format, missing fields, …)
    ├── APIError              – unexpected / non-2xx response from any API
    │   ├── AuthenticationError  – 401 / 403
    │   └── RateLimitError       – 429 (after retries are exhausted)
    └── StorageError          – file I/O failure when persisting results
"""


class PipelineError(Exception):
    """Base class for all Automated Outreach Pipeline exceptions."""


# ── Input / validation ────────────────────────────────────────────────────────


class ValidationError(PipelineError):
    """
    Raised when user-supplied input or an API response fails validation.

    Examples:
      - Malformed domain string.
      - API response missing expected keys.
      - Email address with an invalid format.
    """


# ── API / network ─────────────────────────────────────────────────────────────


class APIError(PipelineError):
    """
    Raised for unexpected API responses that are not covered by more specific
    sub-classes.

    Attributes:
        status_code: HTTP status code of the failing response (if applicable).
        service:     Name of the downstream service (e.g. ``"Ocean.io"``).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        service: str = "",
    ) -> None:
        prefix = f"[{service}] " if service else ""
        super().__init__(f"{prefix}{message}")
        self.status_code = status_code
        self.service = service


class AuthenticationError(APIError):
    """
    Raised when the API returns 401 or 403, indicating an invalid or missing
    API key.
    """

    def __init__(self, service: str = "") -> None:
        super().__init__(
            "Authentication failed. Check that your API key is correct and "
            "has the necessary permissions.",
            status_code=401,
            service=service,
        )


class RateLimitError(APIError):
    """
    Raised when a 429 response persists after all retry attempts have been
    exhausted.
    """

    def __init__(self, message: str = "", service: str = "") -> None:
        super().__init__(
            message or "Rate limit exceeded. Try again later.",
            status_code=429,
            service=service,
        )


# ── Storage ───────────────────────────────────────────────────────────────────


class StorageError(PipelineError):
    """
    Raised when the pipeline cannot persist results to the output file (e.g.
    permission denied, disk full).
    """
