#!/usr/bin/env python3
"""
test_ocean.py
-------------
Standalone diagnostic script for Ocean.io API integration.

Endpoint catalogue sourced from: GET https://api.ocean.io/openapi.json

What it does:
  1. Loads OCEAN_API_KEY from .env
  2. Checks your credit balance (GET /v2/credits/balance)
  3. Probes the correct lookalike endpoint (POST /v3/search/companies)
  4. Falls back to the deprecated v2 endpoint if v3 fails
  5. Prints full request + response details (API key masked)
  6. Summarises: key valid? endpoint working? plan has access?

Usage:
    python test_ocean.py
    python test_ocean.py --domain stripe.com
    python test_ocean.py --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from typing import Any

import requests
from dotenv import load_dotenv

# ── load .env ─────────────────────────────────────────────────────────────────
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

OCEAN_API_KEY = os.environ.get("OCEAN_API_KEY", "").strip()
_BASE = "https://api.ocean.io"  # canonical root — versions appended per endpoint


# ── helpers ───────────────────────────────────────────────────────────────────

def _mask(key: str) -> str:
    """Mask all but first 8 and last 4 chars of an API key."""
    if len(key) <= 12:
        return "***"
    return key[:8] + "..." + key[-4:]


def _print_section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


def _safe_headers(session: requests.Session) -> dict:
    return {
        k: (_mask(v) if "token" in k.lower() or "key" in k.lower() or "auth" in k.lower() else v)
        for k, v in session.headers.items()
    }


def _print_request(method: str, url: str, headers: dict, body: Any | None) -> None:
    print(f"\n  ▶ {method.upper()} {url}")
    print(f"  Headers :\n{textwrap.indent(json.dumps(headers, indent=4), '    ')}")
    if body is not None:
        print(f"  Body    :\n{textwrap.indent(json.dumps(body, indent=4), '    ')}")


def _print_response(resp: requests.Response) -> None:
    print(f"\n  ◀ Status : {resp.status_code} {resp.reason}")
    try:
        body = resp.json()
        pretty = json.dumps(body, indent=4)
    except ValueError:
        pretty = resp.text[:600] or "(empty body)"
    print(f"  Body    :\n{textwrap.indent(pretty, '    ')}")


def _request(
    session: requests.Session,
    method: str,
    url: str,
    body: dict | None = None,
    verbose: bool = False,
) -> requests.Response | None:
    if verbose:
        _print_request(method.upper(), url, _safe_headers(session), body)
    try:
        resp = (
            session.post(url, json=body, timeout=15)
            if method.lower() == "post"
            else session.get(url, timeout=15)
        )
    except requests.exceptions.RequestException as exc:
        print(f"  ✗ Network error: {exc}")
        return None
    if verbose:
        _print_response(resp)
    return resp


# ── endpoint catalogue (from GET https://api.ocean.io/openapi.json) ───────────

#  Payload for lookalike search (v3):
#  {
#    "size": <int>,
#    "fields": [...],
#    "companiesFilters": {
#      "lookalikeDomains": ["<seed_domain>"]   ← NOT "seedDomains"
#    }
#  }

CANDIDATES: list[dict[str, Any]] = [
    # ── Primary (correct) ──────────────────────────────────────────────────────
    {
        "label": "✓ v3/search/companies (POST) — CORRECT lookalike endpoint",
        "method": "POST",
        "url": f"{_BASE}/v3/search/companies",
        "body": {
            "size": 1,
            "fields": ["domain", "name", "companySize", "primaryCountry", "industries"],
            "companiesFilters": {"lookalikeDomains": ["__SEED__"]},
        },
    },
    # ── Auth check ─────────────────────────────────────────────────────────────
    {
        "label": "v2/credits/balance (GET) — auth + credit check",
        "method": "GET",
        "url": f"{_BASE}/v2/credits/balance",
        "body": None,
    },
    # ── Deprecated fallback ────────────────────────────────────────────────────
    {
        "label": "v2/search/companies (POST) — deprecated lookalike",
        "method": "POST",
        "url": f"{_BASE}/v2/search/companies",
        "body": {
            "size": 1,
            "fields": ["domain", "name"],
            "companiesFilters": {"lookalikeDomains": ["__SEED__"]},
        },
    },
    # ── Other useful endpoints ─────────────────────────────────────────────────
    {
        "label": "v2/enrich/company (POST) — company enrichment",
        "method": "POST",
        "url": f"{_BASE}/v2/enrich/company",
        "body": {"domain": "__SEED__"},
    },
    {
        "label": "openapi.json (GET) — API schema discovery",
        "method": "GET",
        "url": f"{_BASE}/openapi.json",
        "body": None,
    },
]


# ── main diagnostic ───────────────────────────────────────────────────────────

def run_diagnostics(seed_domain: str, verbose: bool) -> None:
    _print_section("Ocean.io API Diagnostic")

    if not OCEAN_API_KEY:
        print("\n  ✗ OCEAN_API_KEY is not set in .env — cannot proceed.")
        sys.exit(1)

    print(f"\n  API Key   : {_mask(OCEAN_API_KEY)}")
    print(f"  API Root  : {_BASE}")
    print(f"  Seed      : {seed_domain}")

    session = requests.Session()
    session.headers.update({
        "X-Api-Token": OCEAN_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })

    # ── Step 1: quick auth check ──────────────────────────────────────────────
    _print_section("Step 1 — Authentication Check (GET /v2/credits/balance)")
    auth_resp = _request(session, "GET", f"{_BASE}/v2/credits/balance", verbose=verbose)

    if auth_resp is None or not auth_resp.ok:
        code = auth_resp.status_code if auth_resp is not None else "N/A"
        print(f"\n  ✗ Auth check FAILED (HTTP {code})")
        if auth_resp is not None and auth_resp.status_code in (401, 403):
            print("    → Your OCEAN_API_KEY is invalid or expired.")
            print("    → Regenerate it at: ocean.io → Account Settings → API Tokens")
        sys.exit(1)

    creds = auth_resp.json().get("credits", {})
    daily_left = auth_resp.json().get("dailyLimitRateLeft", "?")
    print(f"\n  ✓ API key is VALID")
    print(f"  Credits   : one-time={creds.get('oneTime', '?')}  "
          f"recurrent={creds.get('recurrent', '?')}")
    print(f"  Daily rate limit remaining: {daily_left}")

    # ── Step 2: probe lookalike endpoint ──────────────────────────────────────
    _print_section("Step 2 — Lookalike Company Search (POST /v3/search/companies)")

    lookalike_url = f"{_BASE}/v3/search/companies"
    payload = {
        "size": 3,
        "fields": ["domain", "name", "companySize", "primaryCountry", "industries"],
        "companiesFilters": {"lookalikeDomains": [seed_domain]},
    }

    print(f"\n  Endpoint  : POST {lookalike_url}")
    print(f"  Payload   :\n{textwrap.indent(json.dumps(payload, indent=4), '    ')}")

    if verbose:
        _print_request("POST", lookalike_url, _safe_headers(session), payload)

    resp = _request(session, "POST", lookalike_url, body=payload, verbose=False)

    if resp is None:
        print("\n  ✗ Network error — cannot reach Ocean.io")
        sys.exit(1)

    print(f"\n  HTTP {resp.status_code} {resp.reason}")

    try:
        data = resp.json()
    except ValueError:
        print(f"  ✗ Non-JSON response: {resp.text[:300]}")
        sys.exit(1)

    if not resp.ok:
        print(f"  ✗ Request failed: {data}")
        if resp.status_code == 402:
            print("    → Insufficient credits.")
        elif resp.status_code == 403:
            print("    → You are not allowed to access this feature.")
            print("    → Check your Ocean.io plan for lookalike/API access.")
        sys.exit(1)

    companies = data.get("companies", [])
    credits_used = data.get("creditsUsed", "?")
    total = data.get("total", "?")
    missing = data.get("missingDomains", {})
    redirect = data.get("redirectMap", {})

    print(f"\n  ✓ SUCCESS — {len(companies)} companies returned (total available: {total})")
    print(f"  Credits used this call : {credits_used}")
    if redirect:
        print(f"  Domain redirects       : {redirect}")
    if missing:
        print(f"  ⚠ Missing/bad domains  : {missing}")

    print("\n  Sample results:")
    for wrapper in companies:
        co = wrapper.get("company", wrapper)
        relevance = wrapper.get("relevance", "?")
        print(f"    [{relevance}] {co.get('name', '?')} ({co.get('domain', '?')}) "
              f"— {co.get('companySize', '?')} employees — "
              f"{co.get('primaryCountry', '?')}")
        industries = co.get("industries", [])
        if industries:
            print(f"       Industries: {', '.join(industries[:4])}")

    # ── Step 3: summary ───────────────────────────────────────────────────────
    _print_section("Summary")
    print("\n  ✓ API key             : VALID")
    print(f"  ✓ Lookalike endpoint  : POST {lookalike_url}")
    print(f"  ✓ Payload key         : companiesFilters.lookalikeDomains (NOT seedDomains)")
    print(f"  ✓ Auth header         : X-Api-Token (NOT Authorization: Bearer)")
    print(f"  ✓ Companies returned  : {len(companies)} (of {total} total)")
    print(f"  ✓ Credits used        : {credits_used}")
    print(f"  ✓ Remaining (one-time): {creds.get('oneTime', '?')}")
    print(f"  ✓ Remaining (monthly) : {creds.get('recurrent', '?')}")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Ocean.io API diagnostic script")
    parser.add_argument(
        "--domain", default="notion.so",
        help="Seed domain for lookalike probe (default: notion.so)"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print full request/response headers and bodies"
    )
    args = parser.parse_args()
    run_diagnostics(args.domain, args.verbose)


if __name__ == "__main__":
    main()
