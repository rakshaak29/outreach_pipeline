"""
utils/validators.py
-------------------
Input-validation helpers for the Automated Outreach Pipeline.

All validators raise the project's custom ValidationError on failure so that
callers can handle them uniformly without catching built-in exceptions.
"""

import re

from exceptions import ValidationError

# ── domain validation ─────────────────────────────────────────────────────────
# Matches bare domains such as "notion.so", "sub.example.co.uk".
# Rejects anything containing a path, query string, or scheme.
_DOMAIN_RE = re.compile(
    r"^(?:[a-zA-Z0-9]"           # starts with alphanumeric
    r"(?:[a-zA-Z0-9\-]{0,61}"    # optional middle chars
    r"[a-zA-Z0-9])?\.)+"         # at least one dot-separated label
    r"[a-zA-Z]{2,}$"             # TLD: 2+ alpha chars
)

# RFC-5322 simplified e-mail check (no external libs).
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@"
    r"[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)


def validate_domain(raw: str) -> str:
    """
    Sanitise and validate a company domain string.

    Strips leading ``http(s)://``, trailing slashes and whitespace, then
    checks that the result looks like a valid domain.

    Args:
        raw: User-supplied domain string, e.g. ``"https://notion.so/"`` or
             ``"notion.so"``.

    Returns:
        Clean, lower-cased domain string, e.g. ``"notion.so"``.

    Raises:
        ValidationError: If the sanitised string is not a valid domain.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValidationError("Domain must be a non-empty string.")

    domain = raw.strip().lower()
    # strip scheme
    domain = re.sub(r"^https?://", "", domain)
    # strip path, query, fragment
    domain = domain.split("/")[0].split("?")[0].split("#")[0]
    # strip www. prefix (optional – keeps things uniform)
    domain = re.sub(r"^www\.", "", domain)

    if not _DOMAIN_RE.match(domain):
        raise ValidationError(
            f"'{domain}' is not a valid domain. "
            "Expected format: 'example.com' or 'sub.example.co.uk'."
        )
    return domain


def validate_email(email: str) -> bool:
    """
    Lightweight RFC-5322 e-mail format check.

    Does **not** perform DNS/MX lookup.  Intended for pre-flight filtering
    of obviously malformed addresses from API responses.

    Args:
        email: E-mail address string.

    Returns:
        ``True`` if the format is plausible, ``False`` otherwise.
    """
    if not isinstance(email, str):
        return False
    return bool(_EMAIL_RE.match(email.strip()))


def validate_api_response(data: dict, required_keys: list[str], context: str = "") -> None:
    """
    Assert that a raw API response dict contains all expected top-level keys.

    Args:
        data:          Parsed JSON response dictionary.
        required_keys: Keys that must be present.
        context:       Optional human-readable label for error messages
                       (e.g. ``"Ocean.io /companies/lookalike"``).

    Raises:
        ValidationError: If any required key is absent.
    """
    missing = [k for k in required_keys if k not in data]
    if missing:
        label = f" [{context}]" if context else ""
        raise ValidationError(
            f"API response{label} is missing required keys: {missing}. "
            f"Got keys: {list(data.keys())}"
        )
