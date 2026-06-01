"""
models/company.py
-----------------
Pydantic model representing a company discovered by the Automated Outreach
Pipeline (typically via the Ocean.io lookalike API).

The model is designed to be serialisable to / from JSON and to carry every
field that either the Ocean.io API or downstream processing may provide.
Optional fields default to ``None`` so that partial API responses are
handled gracefully.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


# Valid values for the contacts_fetch_status field.
ContactsFetchStatus = Literal["success", "rate_limited", "failed", "skipped"]


class Company(BaseModel):
    """
    Represents a single company returned by the Ocean.io lookalike search.

    Attributes:
        domain:                Primary internet domain (e.g. ``"notion.so"``).
        name:                  Human-readable company name.
        industry:              Primary industry / vertical (if provided by API).
        employee_count:        Approximate headcount band or exact number.
        location:              Country or region of the company HQ.
        website:               Full website URL (may differ from ``domain``).
        description:           Short company description from the API.
        similarity_score:      Cosine/proprietary similarity score (0–1).
        contacts_fetch_status: Result of the Prospeo contact-lookup step.
                               One of: ``success`` | ``rate_limited`` |
                               ``failed`` | ``skipped``.
        fetched_at:            UTC timestamp set at object creation.
    """

    domain: str = Field(..., description="Primary company domain.")
    name: str = Field(..., description="Company display name.")
    industry: Optional[str] = Field(None, description="Primary industry / sector.")
    employee_count: Optional[int] = Field(None, description="Approximate headcount.")
    location: Optional[str] = Field(None, description="Country or city of HQ.")
    website: Optional[str] = Field(None, description="Full website URL.")
    description: Optional[str] = Field(None, description="Short company bio.")
    similarity_score: Optional[float] = Field(
        None, description="Lookalike similarity score (0–1) or relevance grade."
    )
    contacts_fetch_status: Optional[ContactsFetchStatus] = Field(
        None,
        description=(
            "Result of the Prospeo contact-lookup step. "
            "Values: success | rate_limited | failed | skipped."
        ),
    )
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when this record was fetched.",
    )

    # ── validators ────────────────────────────────────────────────────────────

    @field_validator("domain", mode="before")
    @classmethod
    def normalise_domain(cls, v: str) -> str:
        """Strip scheme and trailing slashes from domains."""
        import re
        v = str(v).strip().lower()
        v = re.sub(r"^https?://", "", v).rstrip("/")
        return v

    @field_validator("employee_count", mode="before")
    @classmethod
    def coerce_employee_count(cls, v: object) -> Optional[int]:
        """
        Ocean.io sometimes returns employee count as a string range
        (e.g. ``"51-200"``).  We keep only the lower bound in that case.
        """
        if v is None:
            return None
        if isinstance(v, int):
            return v
        try:
            return int(str(v).split("-")[0].replace(",", "").strip())
        except (ValueError, AttributeError):
            return None

    @field_validator("similarity_score", mode="before")
    @classmethod
    def coerce_similarity_score(cls, v: object) -> Optional[float]:
        """Accept Ocean.io v3 relevance letter grades and convert to 0–1 floats.

        Ocean.io v3 returns ``"relevance": "A" | "B" | "C" | "D"`` instead of
        a numeric score.  We map these to descending floats so downstream code
        can still sort / filter numerically if needed.

        Grade mapping:
            A → 1.0,  B → 0.8,  C → 0.6,  D → 0.4,  other → 0.2
        """
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        grade_map = {"A": 1.0, "B": 0.8, "C": 0.6, "D": 0.4}
        upper = str(v).strip().upper()
        if upper in grade_map:
            return grade_map[upper]
        # Try parsing as a numeric string (e.g. "0.93")
        try:
            return float(upper)
        except ValueError:
            return None

    # ── helpers ───────────────────────────────────────────────────────────────

    def display(self) -> str:
        """Return a human-readable one-liner summary of the company."""
        parts = [self.name, f"({self.domain})"]
        if self.industry:
            parts.append(f"| {self.industry}")
        if self.employee_count:
            parts.append(f"| ~{self.employee_count} employees")
        if self.location:
            parts.append(f"| {self.location}")
        return " ".join(parts)

    class Config:
        # Allow population from ORM-style attribute access (future-proofing).
        from_attributes = True
