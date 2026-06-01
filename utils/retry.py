"""
utils/retry.py
--------------
Retry decorator with exponential back-off for transient API errors.

Retries are triggered by:
  - requests.exceptions.RequestException  (network-level errors)
  - HTTP status codes 429 (Rate Limit) and 5xx (Server Error)

Any other exception propagates immediately without retrying.

Usage:
    from utils.retry import retry

    @retry(max_attempts=3, backoff_factor=1.5)
    def call_api() -> dict:
        ...
"""

import functools
import time
from typing import Any, Callable, TypeVar

import requests

from exceptions import RateLimitError
from utils.logger import get_logger

logger = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# HTTP status codes that should trigger a retry
_RETRYABLE_STATUSES: frozenset[int] = frozenset(
    [429, 500, 502, 503, 504]
)


def retry(
    max_attempts: int = 3,
    backoff_factor: float = 1.5,
    retryable_exceptions: tuple[type[Exception], ...] = (requests.exceptions.RequestException,),
) -> Callable[[F], F]:
    """
    Decorator factory that retries the wrapped callable on transient failures.

    Args:
        max_attempts:         Total number of attempts (including the first).
        backoff_factor:       Base multiplier for the exponential wait between
                              retries: ``wait = backoff_factor ** attempt_index``.
        retryable_exceptions: Exception types that should trigger a retry.

    Returns:
        A decorator that wraps the target function.

    Raises:
        RateLimitError: After exhausting all attempts when a 429 was the last
                        error.
        requests.exceptions.RequestException: After exhausting all attempts for
                                              other network errors.
        Exception: Immediately for any non-retryable exception type.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)

                except requests.exceptions.HTTPError as exc:
                    status = exc.response.status_code if exc.response is not None else None
                    if status not in _RETRYABLE_STATUSES:
                        # Non-retryable HTTP error — re-raise immediately.
                        raise

                    last_exc = exc
                    if attempt == max_attempts:
                        break

                    wait = backoff_factor ** attempt
                    logger.warning(
                        "HTTP %s from %s – attempt %d/%d, retrying in %.1fs …",
                        status,
                        func.__qualname__,
                        attempt,
                        max_attempts,
                        wait,
                    )
                    time.sleep(wait)

                except retryable_exceptions as exc:  # type: ignore[misc]
                    last_exc = exc
                    if attempt == max_attempts:
                        break

                    wait = backoff_factor ** attempt
                    logger.warning(
                        "%s from %s – attempt %d/%d, retrying in %.1fs …",
                        type(exc).__name__,
                        func.__qualname__,
                        attempt,
                        max_attempts,
                        wait,
                    )
                    time.sleep(wait)

            # All attempts exhausted.
            assert last_exc is not None  # type-narrowing

            # Wrap 429 exhaustion in a domain-specific error.
            if (
                isinstance(last_exc, requests.exceptions.HTTPError)
                and last_exc.response is not None
                and last_exc.response.status_code == 429
            ):
                raise RateLimitError(
                    f"Rate limit hit for {func.__qualname__} after "
                    f"{max_attempts} attempts."
                ) from last_exc

            raise last_exc

        return wrapper  # type: ignore[return-value]

    return decorator
