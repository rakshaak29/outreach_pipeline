"""
services/prospeo_service.py
----------------------------
Prospeo API client for the Automated Outreach Pipeline.

Workflow (two-step):
    1. POST /search-person
       Searches for contacts at a given company domain with seniority /
       title filters.  Returns a list of ``person_id`` values plus basic
       metadata (name, title, etc.).

    2. POST /bulk-enrich-person  (if persons were found in step 1)
       Accepts a list of ``person_id`` values and returns verified emails,
       LinkedIn URLs, and full contact details.

Authentication:
    Header: ``X-KEY: <PROSPEO_API_KEY>``

Reference:
    https://api.prospeo.io  (requires Prospeo account)
"""

from __future__ import annotations

import json
from typing import Any

import requests

from config import Config
from models.contact import Contact
from exceptions import APIError, AuthenticationError, RateLimitError
from utils.logger import get_logger
from utils.retry import retry

logger = get_logger(__name__)

# Title / seniority keywords to filter decision-makers.
_TARGET_TITLES: frozenset[str] = frozenset(
    {
        "ceo", "cto", "coo", "cmo", "cfo",
        "founder", "co-founder", "cofounder",
        "vp engineering", "vp sales", "vp product", "vp marketing",
        "vice president", "director",
        "head of engineering", "head of sales", "head of product",
        "president",
    }
)

_TARGET_SENIORITY: list[str] = ["c_suite", "vp", "director", "manager", "owner"]


class ProspeoService:
    """
    Two-step Prospeo wrapper: search for contacts, then enrich with emails.

    Args:
        config: Application configuration (injected at construction time).
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._session = requests.Session()
        self._session.headers.update(
            {
                "X-KEY": config.prospeo_api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    # ── public interface ──────────────────────────────────────────────────────

    def find_contacts(
        self,
        company_domain: str,
        limit: int | None = None,
    ) -> list[Contact]:
        """
        Find decision-maker contacts at ``company_domain``.

        Steps:
            1. Search ``/search-person`` with domain + seniority filters.
            2. Enrich the top ``limit`` results via ``/bulk-enrich-person``.
            3. Deduplicate by LinkedIn URL, then by email.

        Args:
            company_domain: Target company domain (e.g. ``"coda.io"``).
            limit:          Maximum contacts to return.
                            Defaults to ``config.max_contacts_per_company``.

        Returns:
            Deduplicated list of :class:`~models.contact.Contact` objects.

        Raises:
            AuthenticationError: On 401 / 403 responses.
            APIError:            On unexpected non-2xx responses.
        """
        effective_limit = (
            limit if limit is not None else self._config.max_contacts_per_company
        )
        logger.info(
            "ProspeoService: searching contacts at '%s' (limit=%d) …",
            company_domain,
            effective_limit,
        )

        # Step 1 — search
        raw_persons = self._search_persons(company_domain, effective_limit)
        if not raw_persons:
            logger.warning(
                "ProspeoService: no contacts found at '%s'.", company_domain
            )
            return []

        # Step 2 — enrich (get emails + full details)
        person_ids = [p["person_id"] for p in raw_persons if p.get("person_id")]
        enriched: list[dict[str, Any]] = []
        if person_ids:
            enriched = self._bulk_enrich(person_ids)

        # Merge search metadata with enrichment data
        contacts = self._merge_and_parse(
            raw_persons, enriched, company_domain
        )
        contacts = self._deduplicate(contacts)
        contacts = contacts[:effective_limit]

        logger.info(
            "ProspeoService: found %d unique contacts at '%s'.",
            len(contacts),
            company_domain,
        )
        return contacts

    # ── private helpers ───────────────────────────────────────────────────────

    @retry()
    def _search_persons(
        self, domain: str, size: int
    ) -> list[dict[str, Any]]:
        """
        POST /search-person with company domain and seniority filters.
        """
        url = f"{self._config.prospeo_base_url}/search-person"
        payload: dict[str, Any] = {
            "filters": {
                "company": domain,
                "person_seniority": _TARGET_SENIORITY,
            },
            "page": 1,
        }

        logger.debug(
            "ProspeoService REQUEST | method=POST | url=%s | body=%s",
            url,
            json.dumps(payload),
        )

        try:
            response = self._session.post(url, json=payload, timeout=30)
        except requests.exceptions.RequestException as exc:
            raise APIError(
                f"Network error contacting Prospeo: {exc}",
                service="Prospeo",
            ) from exc

        logger.debug(
            "ProspeoService RESPONSE | status=%d | body=%s",
            response.status_code,
            response.text[:400],
        )
        self._raise_for_status(response)

        try:
            data = response.json()
        except ValueError as exc:
            raise APIError(
                f"Prospeo returned non-JSON response: {response.text[:200]}",
                service="Prospeo",
            ) from exc

        results: list[dict[str, Any]] = data.get("results", [])
        # Return only the first `size` matching records.
        return results[:size]

    @retry()
    def _bulk_enrich(self, person_ids: list[str]) -> list[dict[str, Any]]:
        """
        POST /bulk-enrich-person to obtain verified emails and full profiles.
        """
        url = f"{self._config.prospeo_base_url}/bulk-enrich-person"
        payload: dict[str, Any] = {"person_id_list": person_ids}

        logger.debug(
            "ProspeoService REQUEST | method=POST | url=%s | enriching %d persons",
            url,
            len(person_ids),
        )

        try:
            response = self._session.post(url, json=payload, timeout=60)
        except requests.exceptions.RequestException as exc:
            raise APIError(
                f"Network error contacting Prospeo (enrich): {exc}",
                service="Prospeo",
            ) from exc

        logger.debug(
            "ProspeoService RESPONSE | status=%d | body=%s",
            response.status_code,
            response.text[:400],
        )
        self._raise_for_status(response)

        try:
            data = response.json()
        except ValueError as exc:
            raise APIError(
                f"Prospeo enrichment returned non-JSON: {response.text[:200]}",
                service="Prospeo",
            ) from exc

        return data.get("results", data if isinstance(data, list) else [])

    def _raise_for_status(self, response: requests.Response) -> None:
        """Map HTTP error codes to domain-specific exceptions.

        - 401 / 403 → AuthenticationError (pipeline should abort)
        - 429       → RateLimitError      (pipeline should skip & continue)
        - other 4xx → APIError            (pipeline should skip & continue)
        - 5xx       → APIError            (retried by @retry decorator)
        """
        if response.status_code in (401, 403):
            raise AuthenticationError(service="Prospeo")
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "unknown")
            raise RateLimitError(
                f"HTTP 429: rate limit exceeded (Retry-After: {retry_after}).",
                service="Prospeo",
            )
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            raise APIError(
                f"HTTP {response.status_code}: {response.text[:300]}",
                status_code=response.status_code,
                service="Prospeo",
            ) from exc

    @staticmethod
    def _merge_and_parse(
        raw_persons: list[dict[str, Any]],
        enriched: list[dict[str, Any]],
        company_domain: str,
    ) -> list[Contact]:
        """
        Combine search results with enrichment data into Contact objects.
        """
        # Build a lookup map: person_id → enriched data
        enrich_map: dict[str, dict[str, Any]] = {
            e.get("person_id", ""): e for e in enriched
        }

        contacts: list[Contact] = []
        for person in raw_persons:
            pid = person.get("person_id", "")
            enrichment = enrich_map.get(pid, {})
            merged = {**person, **enrichment}

            first = merged.get("first_name", "")
            last = merged.get("last_name", "")
            full = merged.get("full_name") or f"{first} {last}".strip() or "Unknown"

            # Extract email – Prospeo wraps email in a nested dict sometimes
            email_raw = merged.get("email")
            if isinstance(email_raw, dict):
                email = email_raw.get("email") or email_raw.get("value")
            else:
                email = email_raw

            try:
                contact = Contact(
                    full_name=full,
                    first_name=first or None,
                    last_name=last or None,
                    email=email,
                    title=merged.get("job_title") or merged.get("title"),
                    linkedin_url=merged.get("linkedin_url") or merged.get("linkedin"),
                    company_domain=company_domain,
                    seniority=merged.get("person_seniority") or merged.get("seniority"),
                    department=merged.get("department"),
                    person_id=pid or None,
                )
                contacts.append(contact)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ProspeoService: skipping malformed contact (%s): %s",
                    type(exc).__name__,
                    exc,
                )
        return contacts

    @staticmethod
    def _deduplicate(contacts: list[Contact]) -> list[Contact]:
        """
        Remove duplicates: first by LinkedIn URL, then by email address.
        Contacts without either key are always kept (cannot be compared).
        """
        seen_linkedin: set[str] = set()
        seen_email: set[str] = set()
        unique: list[Contact] = []

        for contact in contacts:
            li = contact.dedup_key_linkedin
            em = contact.dedup_key_email

            if li and li in seen_linkedin:
                continue
            if em and em in seen_email:
                continue

            if li:
                seen_linkedin.add(li)
            if em:
                seen_email.add(em)
            unique.append(contact)

        return unique

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "ProspeoService":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
