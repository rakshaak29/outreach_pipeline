"""
config.py
---------
Centralised configuration loader for the Automated Outreach Pipeline.

Loads environment variables from a ``.env`` file (if present) using
``python-dotenv``, then exposes them as a typed, validated ``Config``
dataclass.

The module raises ``EnvironmentError`` at import time if any required
variable is absent, so the pipeline never starts with a broken config.

Usage:
    from config import get_config

    cfg = get_config()          # cached singleton
    print(cfg.ocean_api_key)
"""

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

# Load .env from the project root (next to this file).
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))


# ── helper ────────────────────────────────────────────────────────────────────

def _require(key: str) -> str:
    """Return the env-var value or raise EnvironmentError."""
    value = os.environ.get(key, "").strip()
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{key}' is missing or empty.\n"
            "Copy .env.example to .env and fill in your API keys."
        )
    return value


def _optional(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _optional_int(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return default


def _optional_float(key: str, default: float) -> float:
    raw = os.environ.get(key, "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return default


# ── Config dataclass ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Config:
    """
    Immutable configuration snapshot loaded from environment variables.

    Attributes:
        ocean_api_key:            Ocean.io API token (required).
        prospeo_api_key:          Prospeo API key (required).
        ocean_base_url:           Base URL for the Ocean.io v3 API.
        prospeo_base_url:         Base URL for the Prospeo v1 API.
        max_similar_companies:    Maximum companies to fetch per run (default 10).
        max_contacts_per_company: Maximum contacts per company (default 5).
        retry_max_attempts:       Total HTTP attempts before giving up (default 3).
        retry_backoff_factor:     Exponential backoff base in seconds (default 1.5).
        output_file:              Path to the JSON results file.
        log_level:                Python logging level name (default ``INFO``).
    """

    # required
    ocean_api_key: str
    prospeo_api_key: str

    # optional with defaults
    ocean_base_url: str = "https://api.ocean.io/v3"
    prospeo_base_url: str = "https://api.prospeo.io"
    max_similar_companies: int = 10
    max_contacts_per_company: int = 5
    retry_max_attempts: int = 3
    retry_backoff_factor: float = 1.5
    output_file: str = field(default="data/results.json")
    log_level: str = "INFO"


# ── public API ────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_config() -> Config:
    """
    Build and return the singleton ``Config`` object.

    The result is cached — subsequent calls return the same instance, so
    callers can call this freely without worrying about re-loading env vars.

    Raises:
        EnvironmentError: If ``OCEAN_API_KEY`` or ``PROSPEO_API_KEY`` are
                          not set.
    """
    return Config(
        ocean_api_key=_require("OCEAN_API_KEY"),
        prospeo_api_key=_require("PROSPEO_API_KEY"),
        ocean_base_url=_optional(
            "OCEAN_BASE_URL", "https://api.ocean.io/v3"
        ),
        prospeo_base_url=_optional(
            "PROSPEO_BASE_URL", "https://api.prospeo.io"
        ),
        max_similar_companies=_optional_int("MAX_SIMILAR_COMPANIES", 10),
        max_contacts_per_company=_optional_int("MAX_CONTACTS_PER_COMPANY", 5),
        retry_max_attempts=_optional_int("RETRY_MAX_ATTEMPTS", 3),
        retry_backoff_factor=_optional_float("RETRY_BACKOFF_FACTOR", 1.5),
        output_file=_optional("OUTPUT_FILE", "data/results.json"),
        log_level=_optional("LOG_LEVEL", "INFO").upper(),
    )
