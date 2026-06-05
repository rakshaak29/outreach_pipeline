"""
db/database.py
--------------
SQLite persistence layer for the Automated Outreach Pipeline.

Schema
------
pipeline_runs
    run_id          TEXT  PRIMARY KEY
    source_domain   TEXT  NOT NULL
    timestamp       TEXT  NOT NULL          -- ISO-8601 UTC
    dry_run         INTEGER NOT NULL        -- 0 / 1
    ocean_only      INTEGER NOT NULL        -- 0 / 1
    companies_found INTEGER NOT NULL
    contacts_found  INTEGER NOT NULL

companies
    id                    INTEGER PRIMARY KEY AUTOINCREMENT
    run_id                TEXT    NOT NULL REFERENCES pipeline_runs(run_id)
    domain                TEXT    NOT NULL
    name                  TEXT    NOT NULL
    industry              TEXT
    employee_count        INTEGER
    location              TEXT
    website               TEXT
    description           TEXT
    similarity_score      REAL
    contacts_fetch_status TEXT
    fetched_at            TEXT    NOT NULL

contacts
    id               INTEGER PRIMARY KEY AUTOINCREMENT
    run_id           TEXT    NOT NULL REFERENCES pipeline_runs(run_id)
    company_domain   TEXT    NOT NULL
    full_name        TEXT    NOT NULL
    first_name       TEXT
    last_name        TEXT
    email            TEXT
    title            TEXT
    linkedin_url     TEXT
    seniority        TEXT
    department       TEXT
    person_id        TEXT
    outreach_message TEXT
    fetched_at       TEXT    NOT NULL

Usage
-----
    from db.database import Database

    db = Database()                         # auto-creates database.db
    db = Database("path/to/custom.db")

    db.save_run(run_id, source_domain, timestamp, dry_run, ocean_only,
                companies_found, contacts_found)
    db.save_companies(run_id, companies)
    db.save_contacts(run_id, contacts_by_domain)

    rows = db.get_all_companies()
    rows = db.get_all_contacts()
    db.close()
"""

from __future__ import annotations

import csv
import os
import sqlite3
from typing import Any

from models.company import Company
from models.contact import Contact
from utils.logger import get_logger

logger = get_logger(__name__)

# Default DB path (project root / database.db)
_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "database.db"
)

# ── DDL ───────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id          TEXT    PRIMARY KEY,
    source_domain   TEXT    NOT NULL,
    timestamp       TEXT    NOT NULL,
    dry_run         INTEGER NOT NULL DEFAULT 0,
    ocean_only      INTEGER NOT NULL DEFAULT 0,
    companies_found INTEGER NOT NULL DEFAULT 0,
    contacts_found  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS companies (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                TEXT    NOT NULL REFERENCES pipeline_runs(run_id),
    domain                TEXT    NOT NULL,
    name                  TEXT    NOT NULL,
    industry              TEXT,
    employee_count        INTEGER,
    location              TEXT,
    website               TEXT,
    description           TEXT,
    similarity_score      REAL,
    contacts_fetch_status TEXT,
    fetched_at            TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT    NOT NULL REFERENCES pipeline_runs(run_id),
    company_domain   TEXT    NOT NULL,
    full_name        TEXT    NOT NULL,
    first_name       TEXT,
    last_name        TEXT,
    email            TEXT,
    title            TEXT,
    linkedin_url     TEXT,
    seniority        TEXT,
    department       TEXT,
    person_id        TEXT,
    outreach_message TEXT,
    fetched_at       TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_companies_run_id    ON companies(run_id);
CREATE INDEX IF NOT EXISTS idx_companies_domain    ON companies(domain);
CREATE INDEX IF NOT EXISTS idx_contacts_run_id     ON contacts(run_id);
CREATE INDEX IF NOT EXISTS idx_contacts_domain     ON contacts(company_domain);
CREATE INDEX IF NOT EXISTS idx_contacts_email      ON contacts(email);
"""


class Database:
    """
    Thin SQLite wrapper for pipeline persistence.

    All writes use ``INSERT OR REPLACE`` / ``INSERT OR IGNORE`` so running the
    pipeline multiple times for the same domain does not create duplicates in
    ``pipeline_runs``.  Company and contact rows are always inserted fresh per
    run (they carry ``run_id``).

    Args:
        db_path: Path to the SQLite database file.
                 Defaults to ``database.db`` in the project root.
    """

    def __init__(self, db_path: str = _DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row  # dict-like access
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._apply_schema()
        logger.debug("Database: connected to '%s'.", db_path)

    # ── schema ────────────────────────────────────────────────────────────────

    def _apply_schema(self) -> None:
        """Create tables / indexes if they don't exist yet."""
        self._conn.executescript(_DDL)
        self._conn.commit()
        logger.debug("Database: schema applied.")

    # ── writes ────────────────────────────────────────────────────────────────

    def save_run(
        self,
        *,
        run_id: str,
        source_domain: str,
        timestamp: str,
        dry_run: bool,
        ocean_only: bool,
        companies_found: int,
        contacts_found: int,
    ) -> None:
        """Upsert a pipeline run record."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO pipeline_runs
                (run_id, source_domain, timestamp, dry_run, ocean_only,
                 companies_found, contacts_found)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                source_domain,
                timestamp,
                int(dry_run),
                int(ocean_only),
                companies_found,
                contacts_found,
            ),
        )
        self._conn.commit()
        logger.debug("Database: upserted pipeline_run '%s'.", run_id)

    def save_companies(
        self,
        run_id: str,
        companies: list[Company],
    ) -> None:
        """Insert all companies for a run (one row per company)."""
        rows = [
            (
                run_id,
                c.domain,
                c.name,
                c.industry,
                c.employee_count,
                c.location,
                c.website,
                c.description,
                c.similarity_score,
                c.contacts_fetch_status,
                c.fetched_at.isoformat(),
            )
            for c in companies
        ]
        self._conn.executemany(
            """
            INSERT INTO companies
                (run_id, domain, name, industry, employee_count, location,
                 website, description, similarity_score, contacts_fetch_status,
                 fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self._conn.commit()
        logger.debug(
            "Database: inserted %d company row(s) for run '%s'.", len(rows), run_id
        )

    def save_contacts(
        self,
        run_id: str,
        contacts_by_domain: dict[str, list[Contact]],
    ) -> None:
        """Insert all contacts for a run (one row per contact)."""
        rows: list[tuple[Any, ...]] = []
        for domain, contacts in contacts_by_domain.items():
            for c in contacts:
                rows.append(
                    (
                        run_id,
                        domain,
                        c.full_name,
                        c.first_name,
                        c.last_name,
                        c.email,
                        c.title,
                        c.linkedin_url,
                        c.seniority,
                        c.department,
                        c.person_id,
                        c.outreach_message,
                        c.fetched_at.isoformat(),
                    )
                )
        if rows:
            self._conn.executemany(
                """
                INSERT INTO contacts
                    (run_id, company_domain, full_name, first_name, last_name,
                     email, title, linkedin_url, seniority, department,
                     person_id, outreach_message, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._conn.commit()
        logger.debug(
            "Database: inserted %d contact row(s) for run '%s'.", len(rows), run_id
        )

    # ── reads ─────────────────────────────────────────────────────────────────

    def get_all_companies(self) -> list[dict[str, Any]]:
        """Return all company rows as plain dicts, newest run first."""
        cur = self._conn.execute(
            """
            SELECT c.*, r.source_domain AS seed_domain, r.timestamp AS run_timestamp
            FROM   companies c
            JOIN   pipeline_runs r ON r.run_id = c.run_id
            ORDER  BY c.id DESC
            """
        )
        return [dict(row) for row in cur.fetchall()]

    def get_all_contacts(self) -> list[dict[str, Any]]:
        """Return all contact rows as plain dicts, newest run first."""
        cur = self._conn.execute(
            """
            SELECT ct.*, r.source_domain AS seed_domain, r.timestamp AS run_timestamp
            FROM   contacts ct
            JOIN   pipeline_runs r ON r.run_id = ct.run_id
            ORDER  BY ct.id DESC
            """
        )
        return [dict(row) for row in cur.fetchall()]

    def get_all_runs(self) -> list[dict[str, Any]]:
        """Return all pipeline_run rows as plain dicts, newest first."""
        cur = self._conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY timestamp DESC"
        )
        return [dict(row) for row in cur.fetchall()]

    # ── CSV export ────────────────────────────────────────────────────────────

    def export_csv(self, export_dir: str = "exports") -> tuple[str, str]:
        """
        Export all companies and contacts to CSV files.

        Args:
            export_dir: Directory to write CSV files into (created if absent).

        Returns:
            Tuple of ``(companies_path, contacts_path)``.
        """
        os.makedirs(export_dir, exist_ok=True)
        companies_path = os.path.join(export_dir, "companies.csv")
        contacts_path = os.path.join(export_dir, "contacts.csv")

        companies = self.get_all_companies()
        contacts = self.get_all_contacts()

        _write_csv(companies_path, companies, fallback_headers=_COMPANY_HEADERS)
        _write_csv(contacts_path, contacts, fallback_headers=_CONTACT_HEADERS)

        logger.info(
            "Database: exported %d companies → '%s', %d contacts → '%s'.",
            len(companies),
            companies_path,
            len(contacts),
            contacts_path,
        )
        return companies_path, contacts_path

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()
        logger.debug("Database: connection closed.")

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


# ── helpers ───────────────────────────────────────────────────────────────────


def _write_csv(path: str, rows: list[dict[str, Any]], fallback_headers: list[str] | None = None) -> None:
    """Write a list of dicts to a CSV file with a header row.

    If ``rows`` is empty, writes only the header row using ``fallback_headers``
    (if provided) so that the CSV schema is always present.
    """
    if not rows:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            if fallback_headers:
                writer = csv.DictWriter(fh, fieldnames=fallback_headers)
                writer.writeheader()
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# Column lists used as fallback headers when tables are empty.
_COMPANY_HEADERS = [
    "id", "run_id", "domain", "name", "industry", "employee_count",
    "location", "website", "description", "similarity_score",
    "contacts_fetch_status", "fetched_at", "seed_domain", "run_timestamp",
]
_CONTACT_HEADERS = [
    "id", "run_id", "company_domain", "full_name", "first_name", "last_name",
    "email", "title", "linkedin_url", "seniority", "department", "person_id",
    "outreach_message", "fetched_at", "seed_domain", "run_timestamp",
]

