"""
models/contact.py
-----------------
Pydantic model representing a decision-maker contact discovered by the
Automated Outreach Pipeline (typically via the Prospeo API).

Contacts are linked to a :class:`~models.company.Company` via
``company_domain``.  Deduplication keys are ``linkedin_url`` and ``email``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class Contact(BaseModel):
    """
    Represents a single decision-maker at a target company.

    Attributes:
        full_name:      Full display name (first + last).
        first_name:     Given name (from API).
        last_name:      Family name (from API).
        email:          Verified work email address (may be ``None``).
        title:          Job title (e.g. ``"VP Engineering"``).
        linkedin_url:   Canonical LinkedIn profile URL (deduplication key).
        company_domain: Domain of the employer – links to a ``Company``.
        seniority:      Seniority band returned by Prospeo (e.g. ``"senior"``).
        department:     Department string (e.g. ``"engineering"``).
        person_id:      Prospeo internal identifier (used for enrichment calls).
        outreach_message: Generated outreach message (populated later).
        fetched_at:     UTC timestamp set at object creation.
    """

    full_name: str = Field(..., description="Full display name.")
    first_name: Optional[str] = Field(None, description="Given name.")
    last_name: Optional[str] = Field(None, description="Family name.")
    email: Optional[str] = Field(None, description="Verified work email.")
    title: Optional[str] = Field(None, description="Job title.")
    linkedin_url: Optional[str] = Field(None, description="LinkedIn profile URL.")
    company_domain: str = Field(..., description="Domain of the employer company.")
    seniority: Optional[str] = Field(None, description="Seniority level.")
    department: Optional[str] = Field(None, description="Department / function.")
    person_id: Optional[str] = Field(None, description="Prospeo person_id for enrichment.")
    outreach_message: Optional[str] = Field(
        None, description="Generated outreach message (set by OutreachService)."
    )
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when this record was fetched.",
    )

    # ── validators ────────────────────────────────────────────────────────────

    @field_validator("linkedin_url", mode="before")
    @classmethod
    def normalise_linkedin(cls, v: object) -> Optional[str]:
        """Strip tracking parameters and trailing slashes."""
        if v is None:
            return None
        url = str(v).strip().split("?")[0].rstrip("/")
        return url or None

    @field_validator("email", mode="before")
    @classmethod
    def normalise_email(cls, v: object) -> Optional[str]:
        if v is None:
            return None
        email = str(v).strip().lower()
        return email or None

    @field_validator("full_name", mode="before")
    @classmethod
    def build_full_name(cls, v: object) -> str:
        """Accept either an explicit full_name or fall back to a placeholder."""
        if isinstance(v, str) and v.strip():
            return v.strip()
        return "Unknown Contact"

    # ── properties ────────────────────────────────────────────────────────────

    @property
    def dedup_key_linkedin(self) -> Optional[str]:
        """Canonical deduplication key: normalised LinkedIn URL."""
        return self.linkedin_url

    @property
    def dedup_key_email(self) -> Optional[str]:
        """Canonical deduplication key: lower-cased email."""
        return self.email

    # ── helpers ───────────────────────────────────────────────────────────────

    def display(self) -> str:
        """Return a human-readable one-liner summary of the contact."""
        parts = [self.full_name]
        if self.title:
            parts.append(f"– {self.title}")
        parts.append(f"@ {self.company_domain}")
        if self.email:
            parts.append(f"<{self.email}>")
        return " ".join(parts)

    class Config:
        from_attributes = True
