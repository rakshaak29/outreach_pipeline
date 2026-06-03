"""
services/ocean_service.py
--------------------------
Ocean.io API client for the Automated Outreach Pipeline.

Endpoint used:
    POST https://api.ocean.io/v3/search/companies

Authentication:
    Header: ``X-Api-Token: <OCEAN_API_KEY>``

Request payload:
    {
        "size": <int>,
        "fields": [...],
        "companiesFilters": {
            "lookalikeDomains": ["<seed_domain>"]
        }
    }

Response shape (200 OK):
    {
        "detail": "OK",
        "total": <int>,
        "creditsUsed": <float>,
        "companies": [
            {"company": {"domain": ..., "name": ..., ...}, "relevance": "A"}
        ]
    }

The ``companies`` array contains wrapper objects — each has a nested ``company``
key holding the actual company data, plus a ``relevance`` score.

Full API schema: GET https://api.ocean.io/openapi.json
Reference: https://api.ocean.io/  (requires Ocean.io account)
"""

from __future__ import annotations

import json
from typing import Any

import requests

from config import Config
from models.company import Company
from exceptions import APIError, AuthenticationError
from utils.logger import get_logger
from utils.retry import retry

logger = get_logger(__name__)

# Target fields requested from Ocean.io (minimises response payload).
# Full list available at: GET https://api.ocean.io/v2/data-fields
_REQUESTED_FIELDS: list[str] = [
    "domain",
    "name",
    "companySize",
    "primaryCountry",
    "industries",
    "description",
    "rootUrl",
]


class OceanService:
    """
    Thin wrapper around the Ocean.io v3 lookalike-companies endpoint.

    Args:
        config: Application configuration (injected at construction time).
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._session = requests.Session()
        self._session.headers.update(
            {
                # Ocean.io uses X-Api-Token (NOT Authorization: Bearer).
                # See: GET https://api.ocean.io/openapi.json
                "X-Api-Token": config.ocean_api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    # ── public interface ──────────────────────────────────────────────────────

    def find_similar_companies(
        self,
        seed_domain: str,
        limit: int | None = None,
    ) -> list[Company]:
        """
        Discover companies similar to ``seed_domain``.

        Internally calls:
            ``POST /v3/companies/lookalike``

        Args:
            seed_domain: The company domain to use as a seed
                         (e.g. ``"notion.so"``).
            limit:       Maximum number of companies to return.
                         Defaults to ``config.max_similar_companies``.

        Returns:
            Deduplicated list of :class:`~models.company.Company` objects.
            Returns ``[]`` if the API yields no results.

        Raises:
            AuthenticationError: On 401 / 403 responses.
            APIError:            On unexpected non-2xx responses.
        """
        effective_limit = limit if limit is not None else self._config.max_similar_companies
        logger.info(
            "OceanService: searching for companies similar to '%s' (limit=%d) …",
            seed_domain,
            effective_limit,
        )

        raw_results = self._fetch_lookalike(seed_domain, effective_limit)

        if not raw_results:
            logger.warning(
                "OceanService: no similar companies found for '%s'.", seed_domain
            )
            return []

        companies = self._parse_companies(raw_results)
        companies = self._deduplicate(companies)

        logger.info(
            "OceanService: found %d unique similar companies for '%s'.",
            len(companies),
            seed_domain,
        )
        return companies

    # ── private helpers ───────────────────────────────────────────────────────

    @retry()  # uses max_attempts / backoff_factor from decorator defaults
    def _fetch_lookalike(self, seed_domain: str, size: int) -> list[dict[str, Any]]:
        """
        Make the HTTP POST call and return the raw list of company dicts.

        Endpoint: POST /v3/search/companies
        Payload:  {"size": N, "fields": [...], "companiesFilters": {"lookalikeDomains": [domain]}}
        Response: {"detail": "OK", "total": N, "creditsUsed": N,
                   "companies": [{"company": {...}, "relevance": "A"}, ...]}

        The ``@retry`` decorator handles transient network errors and 429s.
        """
        # ── Correct endpoint: /v3/search/companies (NOT /v3/companies/lookalike) ──
        url = f"{self._config.ocean_base_url}/search/companies"

        # ── Correct payload: seed domain goes under companiesFilters.lookalikeDomains ──
        payload: dict[str, Any] = {
            "size": size,
            "fields": _REQUESTED_FIELDS,
            "companiesFilters": {
                "lookalikeDomains": [seed_domain],
            },
        }

        # ── DEBUG: log full request details ──────────────────────────────────
        masked_key = self._config.ocean_api_key[:8] + "..." + self._config.ocean_api_key[-4:]
        logger.debug(
            "OceanService REQUEST | method=POST | url=%s | "
            "headers={X-Api-Token: %s, Content-Type: application/json} | "
            "body=%s",
            url,
            masked_key,
            json.dumps(payload),
        )

        try:
            response = self._session.post(url, json=payload, timeout=30)
        except requests.exceptions.RequestException as exc:
            raise APIError(
                f"Network error contacting Ocean.io: {exc}",
                service="Ocean.io",
            ) from exc

        # ── DEBUG: log full response details ─────────────────────────────────
        logger.debug(
            "OceanService RESPONSE | status=%d | body=%s",
            response.status_code,
            response.text[:500],
        )

        self._raise_for_status(response)

        try:
            data = response.json()
        except ValueError as exc:
            raise APIError(
                f"Ocean.io returned non-JSON response: {response.text[:200]}",
                service="Ocean.io",
            ) from exc

        # ── Parse response: {"companies": [{"company": {...}, "relevance": "A"}, ...]} ──
        raw_wrappers = data.get("companies", [])
        if not isinstance(raw_wrappers, list):
            logger.warning(
                "OceanService: unexpected 'companies' value type: %s", type(raw_wrappers)
            )
            return []

        # Log credit cost and total results available
        credits_used = data.get("creditsUsed", 0)
        total_available = data.get("total", len(raw_wrappers))
        missing = data.get("missingDomains", {})
        logger.debug(
            "OceanService: creditsUsed=%.3f | total=%d | returned=%d | missingDomains=%s",
            credits_used,
            total_available,
            len(raw_wrappers),
            missing,
        )
        if missing:
            logger.warning(
                "OceanService: seed domain had issues — %s", missing
            )

        # Each element is {"company": {...}, "relevance": "A"} — unwrap the inner dict.
        unwrapped: list[dict[str, Any]] = []
        for wrapper in raw_wrappers:
            if isinstance(wrapper, dict) and "company" in wrapper:
                company_dict = wrapper["company"]
                # Attach relevance score so _parse_companies can use it
                company_dict["_relevance"] = wrapper.get("relevance")
                unwrapped.append(company_dict)
            elif isinstance(wrapper, dict):
                # Flat format (older API versions) — use as-is
                unwrapped.append(wrapper)

        return unwrapped

    def _raise_for_status(self, response: requests.Response) -> None:
        """Map HTTP error codes to domain-specific exceptions."""
        if response.status_code in (401, 403):
            raise AuthenticationError(service="Ocean.io")

        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            raise APIError(
                f"HTTP {response.status_code}: {response.text[:300]}",
                status_code=response.status_code,
                service="Ocean.io",
            ) from exc

    @staticmethod
    def _parse_companies(raw: list[dict[str, Any]]) -> list[Company]:
        """Convert raw Ocean.io dicts into validated ``Company`` objects.

        Expected fields from v3/search/companies (after unwrapping the
        ``{"company": {...}, "relevance": "A"}`` wrapper in ``_fetch_lookalike``):
            - domain         : str
            - name           : str
            - companySize    : str  e.g. "51-200"
            - primaryCountry : str  e.g. "us"
            - industries     : list[str]
            - description    : str
            - rootUrl        : str  (website URL)
            - _relevance     : str  e.g. "A" (injected by _fetch_lookalike)
        """
        companies: list[Company] = []
        for item in raw:
            try:
                # v3 field name mapping
                domain = item.get("domain") or item.get("rootUrl") or ""
                name = item.get("name") or item.get("companyName") or domain

                # Industries is a list[str] in v3
                industry_raw = item.get("industries") or item.get("industry")
                if isinstance(industry_raw, list):
                    industry = industry_raw[0] if industry_raw else None
                else:
                    industry = industry_raw or None

                # Relevance injected by _fetch_lookalike ("A", "B", "C", ...)
                relevance = item.get("_relevance")

                company = Company(
                    domain=domain,
                    name=name,
                    industry=industry,
                    employee_count=item.get("companySize") or item.get("employeeCount"),
                    location=item.get("primaryCountry") or item.get("country"),
                    website=item.get("rootUrl") or item.get("website"),
                    description=item.get("description"),
                    # Store relevance grade as a numeric-ish score (A=1.0, B=0.9, …)
                    similarity_score=relevance or item.get("score") or item.get("similarityScore"),
                )
                companies.append(company)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "OceanService: skipping malformed company record (%s): %s",
                    type(exc).__name__,
                    exc,
                )
        return companies

    @staticmethod
    def _deduplicate(companies: list[Company]) -> list[Company]:
        """Remove duplicate companies, keeping the first occurrence by domain."""
        seen: set[str] = set()
        unique: list[Company] = []
        for company in companies:
            if company.domain not in seen:
                seen.add(company.domain)
                unique.append(company)
        return unique

    def close(self) -> None:
        """Release the underlying HTTP session."""
        self._session.close()

    def __enter__(self) -> "OceanService":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
