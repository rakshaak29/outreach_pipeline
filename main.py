"""
main.py
-------
CLI entry point for the Automated Outreach Pipeline.

Usage:
    python main.py --domain notion.so
    python main.py --domain notion.so --dry-run
    python main.py --domain notion.so --ocean-only
    python main.py --domain notion.so --resume
    python main.py --domain notion.so --max-companies 5 --max-contacts 3
    python main.py --domain notion.so --output custom_output.json
    python main.py --export-csv

Pipeline steps:
    1. Validate and sanitise the input domain.
    2. Load config from .env.
    3. Discover similar companies via Ocean.io.
    4. For each company, find decision-maker contacts via Prospeo.
       - 429 Rate-limit  → mark company as "rate_limited", continue.
       - Other API error → mark company as "failed", continue.
       - --ocean-only    → mark every company as "skipped", skip Prospeo entirely.
    5. Generate personalised outreach messages (dry-run: messages only, no send).
    6. Persist all results to:
       a. JSON file (data/results.json)
       b. SQLite database (database.db)
    7. Print a concise summary to stdout.

Resume mode (--resume):
    Loads the previous results.json, skips companies that already have
    contacts_fetch_status == "success", and re-attempts the rest.

Export mode (--export-csv):
    Does NOT run the pipeline. Exports the accumulated SQLite data to:
        exports/companies.csv
        exports/contacts.csv
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from config import get_config
from db.database import Database
from models.company import Company
from models.contact import Contact
from exceptions import PipelineError, RateLimitError, AuthenticationError
from services.ocean_service import OceanService
from services.outreach_service import OutreachService
from services.prospeo_service import ProspeoService
from utils.logger import get_logger
from utils.validators import validate_domain

logger = get_logger(__name__)


# ── CLI ───────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="outreach_pipeline",
        description="Automated Outreach Pipeline — find lookalike companies and "
                    "generate personalised outreach messages.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --domain notion.so
  python main.py --domain notion.so --dry-run
  python main.py --domain notion.so --ocean-only
  python main.py --domain notion.so --resume
  python main.py --domain notion.so --max-companies 5 --max-contacts 3
        """,
    )
    parser.add_argument(
        "--domain",
        required=False,
        default=None,
        metavar="DOMAIN",
        help='Seed company domain to find lookalikes for (e.g. "notion.so").',
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Generate messages but do not send any emails.",
    )
    parser.add_argument(
        "--ocean-only",
        action="store_true",
        default=False,
        help=(
            "Fetch similar companies from Ocean.io only. "
            "Skip Prospeo contact lookup entirely. "
            "All companies will have contacts_fetch_status='skipped'."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help=(
            "Resume from the previous run stored in the output file. "
            "Companies with contacts_fetch_status='success' are kept as-is; "
            "all others are re-attempted."
        ),
    )
    parser.add_argument(
        "--max-companies",
        type=int,
        default=None,
        metavar="N",
        help="Override MAX_SIMILAR_COMPANIES from .env.",
    )
    parser.add_argument(
        "--max-contacts",
        type=int,
        default=None,
        metavar="N",
        help="Override MAX_CONTACTS_PER_COMPANY from .env.",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="FILE",
        help="Override output JSON file path (default: data/results.json).",
    )
    parser.add_argument(
        "--export-csv",
        action="store_true",
        default=False,
        help=(
            "Export all accumulated data from SQLite to CSV files and exit. "
            "Does NOT run the pipeline. "
            "Writes: exports/companies.csv and exports/contacts.csv."
        ),
    )
    parser.add_argument(
        "--db",
        default=None,
        metavar="FILE",
        help="Override SQLite database file path (default: database.db).",
    )
    return parser


# ── resume helpers ────────────────────────────────────────────────────────────


def _load_previous_results(output_file: str) -> dict[str, Any] | None:
    """Load the last run's results.json, returning None if it cannot be read."""
    if not os.path.exists(output_file):
        logger.warning("--resume: no previous results file found at '%s'.", output_file)
        return None
    try:
        with open(output_file, encoding="utf-8") as fh:
            data = json.load(fh)
        logger.info("--resume: loaded previous results from '%s'.", output_file)
        return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("--resume: could not parse previous results (%s) — starting fresh.", exc)
        return None


def _build_resume_state(
    previous: dict[str, Any],
) -> tuple[list[Company], dict[str, list[Contact]]]:
    """
    Parse previous results into Company + Contact objects.

    Returns:
        already_done  : list of Company objects with status == "success"
        contacts_done : dict mapping domain → list[Contact] for done companies
    """
    already_done: list[Company] = []
    contacts_done: dict[str, list[Contact]] = {}

    for co_dict in previous.get("companies", []):
        status = co_dict.get("contacts_fetch_status")
        if status != "success":
            continue  # will be re-attempted
        try:
            raw_contacts = co_dict.pop("contacts", [])
            company = Company.model_validate(co_dict)
            contacts = []
            for c in raw_contacts:
                try:
                    contacts.append(Contact.model_validate(c))
                except Exception:  # noqa: BLE001
                    pass
            already_done.append(company)
            contacts_done[company.domain] = contacts
            logger.info(
                "--resume: keeping '%s' (%d contacts, status=success).",
                company.domain,
                len(contacts),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("--resume: skipping malformed company record: %s", exc)

    return already_done, contacts_done


# ── pipeline output helpers ───────────────────────────────────────────────────


def _build_output(
    *,
    run_id: str,
    source_domain: str,
    dry_run: bool,
    ocean_only: bool,
    companies: list[Company],
    contacts_by_domain: dict[str, list[Contact]],
) -> dict[str, Any]:
    """Assemble the final JSON-serialisable result dictionary."""
    company_dicts: list[dict[str, Any]] = []
    for company in companies:
        company_data = json.loads(company.model_dump_json())
        company_contacts = contacts_by_domain.get(company.domain, [])
        company_data["contacts"] = [
            json.loads(c.model_dump_json()) for c in company_contacts
        ]
        company_dicts.append(company_data)

    rate_limited = sum(
        1 for c in companies if c.contacts_fetch_status == "rate_limited"
    )
    failed = sum(1 for c in companies if c.contacts_fetch_status == "failed")
    skipped = sum(1 for c in companies if c.contacts_fetch_status == "skipped")
    success = sum(1 for c in companies if c.contacts_fetch_status == "success")

    return {
        "run_id": run_id,
        "source_domain": source_domain,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "ocean_only": ocean_only,
        "companies_found": len(companies),
        "total_contacts_found": sum(len(v) for v in contacts_by_domain.values()),
        "contacts_fetch_summary": {
            "success": success,
            "rate_limited": rate_limited,
            "failed": failed,
            "skipped": skipped,
        },
        "companies": company_dicts,
    }


def _save_results(output: dict[str, Any], output_file: str) -> None:
    """Write results to disk, overwriting any previous run."""
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, ensure_ascii=False, default=str)
    logger.info("Results written to '%s'.", output_file)


def _print_summary(output: dict[str, Any], dry_run: bool, ocean_only: bool) -> None:
    """Print a human-readable pipeline summary to stdout."""
    sep = "─" * 60
    fetch_summary = output.get("contacts_fetch_summary", {})

    print(f"\n{sep}")
    print(f"  Automated Outreach Pipeline — Run Summary")
    print(sep)
    print(f"  Run ID     : {output['run_id']}")
    print(f"  Seed Domain: {output['source_domain']}")
    print(f"  Timestamp  : {output['timestamp']}")
    print(f"  Mode       : {'DRY-RUN (no emails sent)' if dry_run else 'LIVE'}"
          f"{' | OCEAN-ONLY (Prospeo skipped)' if ocean_only else ''}")
    print(f"  Companies  : {output['companies_found']}")
    print(f"  Contacts   : {output['total_contacts_found']}")
    if not ocean_only:
        print(f"  Contact fetch status breakdown:")
        print(f"    ✓ success      : {fetch_summary.get('success', 0)}")
        print(f"    ⚠ rate_limited : {fetch_summary.get('rate_limited', 0)}")
        print(f"    ✗ failed       : {fetch_summary.get('failed', 0)}")
        print(f"    – skipped      : {fetch_summary.get('skipped', 0)}")
    print(sep)

    for company_data in output.get("companies", []):
        status = company_data.get("contacts_fetch_status", "")
        status_icon = {
            "success": "✓",
            "rate_limited": "⚠",
            "failed": "✗",
            "skipped": "–",
        }.get(status, " ")
        print(f"\n  {status_icon} 📍 {company_data['name']} ({company_data['domain']})"
              f"  [{status or 'pending'}]")
        for contact in company_data.get("contacts", []):
            email_hint = f" <{contact['email']}>" if contact.get("email") else ""
            title_hint = f" — {contact['title']}" if contact.get("title") else ""
            print(f"     👤 {contact['full_name']}{title_hint}{email_hint}")
            if contact.get("outreach_message"):
                preview = contact["outreach_message"].replace("\n", " ").strip()[:80]
                print(f"        ✉  {preview}…")

    if fetch_summary.get("rate_limited", 0):
        print(f"\n  ⚠  {fetch_summary['rate_limited']} company/ies were rate-limited by Prospeo.")
        print("     Re-run with --resume once your rate limit resets to fetch their contacts.")

    print(f"\n{sep}\n")


# ── contact fetching (with status tracking) ───────────────────────────────────


def _fetch_contacts_for_company(
    company: Company,
    prospeo: ProspeoService,
    outreach: OutreachService,
    max_contacts: int,
    contacts_by_domain: dict[str, list[Contact]],
) -> str:
    """
    Fetch and enrich contacts for one company. Returns the contacts_fetch_status.

    Mutates:
        contacts_by_domain  — populated with fetched contacts (may be [])

    Returns:
        "success"      – contacts fetched (may be an empty list if none found)
        "rate_limited" – Prospeo returned 429
        "failed"       – any other API error
    """
    domain = company.domain
    try:
        contacts = prospeo.find_contacts(domain, limit=max_contacts)

        # Generate outreach messages
        for contact in contacts:
            try:
                message = outreach.process(contact, company)
                contact.outreach_message = message  # type: ignore[misc]
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "OutreachService failed for %s: %s",
                    contact.display(),
                    exc,
                )

        contacts_by_domain[domain] = contacts
        logger.info(
            "Prospeo: fetched %d contact(s) for '%s' [status=success].",
            len(contacts),
            domain,
        )
        return "success"

    except RateLimitError as exc:
        logger.warning(
            "Prospeo rate-limited for '%s' — skipping contacts, "
            "pipeline continues. Re-run with --resume to retry. (%s)",
            domain,
            exc,
        )
        contacts_by_domain[domain] = []
        return "rate_limited"

    except AuthenticationError as exc:
        # Auth errors are fatal — re-raise so the pipeline aborts.
        raise

    except PipelineError as exc:
        logger.warning(
            "Prospeo error for '%s' — skipping contacts, pipeline continues. (%s)",
            domain,
            exc,
        )
        contacts_by_domain[domain] = []
        return "failed"


# ── main ──────────────────────────────────────────────────────────────────────


def run(args: argparse.Namespace) -> int:
    """
    Execute the full pipeline.

    Returns:
        Exit code (0 = success, 1 = pipeline error).
    """
    # ── Export-only mode: no pipeline run — just dump DB to CSV and exit. ────
    if getattr(args, "export_csv", False):
        return _run_export_csv(args)

    # Require --domain for all other modes.
    if not getattr(args, "domain", None):
        print("\n[ERROR] --domain is required unless --export-csv is used.\n",
              file=sys.stderr)
        return 1

    run_id = str(uuid.uuid4())
    logger.info("=" * 60)
    logger.info("Pipeline started | run_id=%s", run_id)
    logger.info("=" * 60)

    # ── 1. Validate domain ───────────────────────────────────────────────────
    try:
        domain = validate_domain(args.domain)
    except PipelineError as exc:
        logger.error("Domain validation failed: %s", exc)
        print(f"\n[ERROR] {exc}\n", file=sys.stderr)
        return 1

    # ── 2. Load config ───────────────────────────────────────────────────────
    try:
        config = get_config()
    except EnvironmentError as exc:
        logger.critical("Configuration error: %s", exc)
        print(f"\n[CONFIG ERROR] {exc}\n", file=sys.stderr)
        return 1

    dry_run: bool = args.dry_run
    ocean_only: bool = args.ocean_only
    resume: bool = args.resume
    output_file: str = args.output or config.output_file
    max_companies: int = args.max_companies or config.max_similar_companies
    max_contacts: int = args.max_contacts or config.max_contacts_per_company
    db_path: str | None = getattr(args, "db", None)  # None → use default

    logger.info(
        "Config loaded | domain=%s | dry_run=%s | ocean_only=%s | resume=%s "
        "| max_companies=%d | max_contacts=%d",
        domain, dry_run, ocean_only, resume, max_companies, max_contacts,
    )

    companies: list[Company] = []
    contacts_by_domain: dict[str, list[Contact]] = {}

    # ── 3. Resume: load already-completed companies ──────────────────────────
    already_done: list[Company] = []
    done_domains: set[str] = set()

    if resume:
        previous = _load_previous_results(output_file)
        if previous:
            already_done, contacts_by_domain = _build_resume_state(previous)
            done_domains = {c.domain for c in already_done}
            logger.info(
                "--resume: %d company/ies already completed, will skip them.",
                len(already_done),
            )

    # ── 4. Find similar companies via Ocean.io ───────────────────────────────
    try:
        with OceanService(config) as ocean:
            fresh_companies = ocean.find_similar_companies(domain, limit=max_companies)
    except PipelineError as exc:
        logger.error("OceanService failed: %s", exc)
        print(f"\n[OCEAN ERROR] {exc}\n", file=sys.stderr)
        return 1

    # Merge: keep already-done first, add fresh ones not already in done set.
    new_companies = [c for c in fresh_companies if c.domain not in done_domains]
    companies = already_done + new_companies

    if not companies:
        logger.warning("No similar companies found. Exiting.")
        print("\nNo similar companies were found. Try a different seed domain.\n")
        return 0

    logger.info(
        "Companies: %d total (%d from resume, %d new from Ocean.io).",
        len(companies),
        len(already_done),
        len(new_companies),
    )

    # ── 5. Fetch contacts (unless --ocean-only) ──────────────────────────────
    if ocean_only:
        logger.info("--ocean-only: skipping Prospeo contact lookup for all companies.")
        for company in new_companies:
            object.__setattr__(company, "contacts_fetch_status", "skipped")
            contacts_by_domain[company.domain] = []
    else:
        outreach = OutreachService(dry_run=dry_run)
        rate_limit_triggered = False

        with ProspeoService(config) as prospeo:
            for company in new_companies:
                if rate_limit_triggered:
                    logger.info(
                        "Prospeo still rate-limited — marking '%s' as rate_limited "
                        "without attempting call.",
                        company.domain,
                    )
                    object.__setattr__(company, "contacts_fetch_status", "rate_limited")
                    contacts_by_domain[company.domain] = []
                    continue

                status = _fetch_contacts_for_company(
                    company, prospeo, outreach, max_contacts, contacts_by_domain
                )
                object.__setattr__(company, "contacts_fetch_status", status)

                if status == "rate_limited":
                    rate_limit_triggered = True

        if rate_limit_triggered:
            rate_limited_count = sum(
                1 for c in companies if c.contacts_fetch_status == "rate_limited"
            )
            logger.warning(
                "Prospeo rate limit hit. %d company/ies marked as rate_limited. "
                "Re-run with --resume once your limit resets.",
                rate_limited_count,
            )

    # ── 6a. Build output dict ────────────────────────────────────────────────
    output = _build_output(
        run_id=run_id,
        source_domain=domain,
        dry_run=dry_run,
        ocean_only=ocean_only,
        companies=companies,
        contacts_by_domain=contacts_by_domain,
    )

    # ── 6b. Persist JSON (always, even partial) ──────────────────────────────
    try:
        _save_results(output, output_file)
    except OSError as exc:
        logger.error("Failed to write results: %s", exc)
        print("\n[STORAGE ERROR] Could not write to file. Printing to stdout:\n")
        print(json.dumps(output, indent=2, default=str))

    # ── 6c. Persist to SQLite ────────────────────────────────────────────────
    db_kwargs = {"db_path": db_path} if db_path else {}
    try:
        with Database(**db_kwargs) as db:
            db.save_run(
                run_id=run_id,
                source_domain=domain,
                timestamp=output["timestamp"],
                dry_run=dry_run,
                ocean_only=ocean_only,
                companies_found=len(companies),
                contacts_found=output["total_contacts_found"],
            )
            db.save_companies(run_id, companies)
            db.save_contacts(run_id, contacts_by_domain)
            logger.info(
                "Database: run '%s' persisted to SQLite (%d companies, %d contacts).",
                run_id,
                len(companies),
                output["total_contacts_found"],
            )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Database: failed to persist to SQLite — data is safe in JSON. (%s)", exc
        )

    # ── 7. Print summary ─────────────────────────────────────────────────────
    _print_summary(output, dry_run, ocean_only)
    logger.info("Pipeline completed successfully | run_id=%s", run_id)
    return 0


def _run_export_csv(args: argparse.Namespace) -> int:
    """
    Export-only mode: read SQLite → write exports/companies.csv + contacts.csv.

    Does not run the pipeline. Exits 0 on success, 1 on error.
    """
    db_path: str | None = getattr(args, "db", None)
    db_kwargs = {"db_path": db_path} if db_path else {}

    try:
        with Database(**db_kwargs) as db:
            companies_path, contacts_path = db.export_csv()

        companies_count = 0
        contacts_count = 0
        try:
            import csv as _csv
            with open(companies_path, encoding="utf-8") as fh:
                companies_count = max(0, sum(1 for _ in fh) - 1)  # subtract header
            with open(contacts_path, encoding="utf-8") as fh:
                contacts_count = max(0, sum(1 for _ in fh) - 1)
        except OSError:
            pass

        sep = "─" * 60
        print(f"\n{sep}")
        print("  CSV Export Complete")
        print(sep)
        print(f"  Companies : {companies_count:>5} rows  →  {companies_path}")
        print(f"  Contacts  : {contacts_count:>5} rows  →  {contacts_path}")
        print(f"{sep}\n")
        return 0

    except Exception as exc:  # noqa: BLE001
        logger.error("CSV export failed: %s", exc)
        print(f"\n[EXPORT ERROR] {exc}\n", file=sys.stderr)
        return 1


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    # --export-csv does not need --domain; patch to avoid argparse errors
    # by making domain optional at the parser level.
    sys.exit(run(args))


if __name__ == "__main__":
    main()
