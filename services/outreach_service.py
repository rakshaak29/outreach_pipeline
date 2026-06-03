"""
services/outreach_service.py
-----------------------------
Outreach message generator for the Automated Outreach Pipeline.

This module is intentionally decoupled from email-sending concerns.
It provides:

  - ``OutreachInterface``  — ABC that any future email adapter must implement.
  - ``OutreachService``    — Generates personalised plain-text messages and,
                             in non-dry-run mode, would invoke an adapter.

Email sending is **not** implemented here.  The ``OutreachInterface.send``
method exists as a forward-compatibility hook.
"""

from __future__ import annotations

import textwrap
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from utils.logger import get_logger

if TYPE_CHECKING:
    from models.company import Company
    from models.contact import Contact

logger = get_logger(__name__)


# ── Interface (forward-compatibility hook) ────────────────────────────────────


class OutreachInterface(ABC):
    """
    Abstract base class for outreach delivery adapters.

    Implement this interface to plug in any delivery channel
    (SMTP, SendGrid, Mailgun, etc.) without touching the generator.

    Example::

        class SmtpAdapter(OutreachInterface):
            def send(self, message: str, contact: Contact) -> None:
                smtp_client.send_email(to=contact.email, body=message)
    """

    @abstractmethod
    def send(self, message: str, contact: "Contact") -> None:
        """
        Deliver ``message`` to the given ``contact``.

        Args:
            message: The plain-text outreach email body.
            contact: The recipient contact record.

        Raises:
            NotImplementedError: This method **must** be overridden.
        """
        ...


# ── Service ───────────────────────────────────────────────────────────────────


class OutreachService:
    """
    Generates personalised outreach messages and (optionally) dispatches them.

    Args:
        adapter:  An optional :class:`OutreachInterface` implementation.
                  When ``None`` (default), generated messages are not sent —
                  this is the expected behaviour during dry-run mode and until
                  email sending is plugged in.
        dry_run:  If ``True``, messages are generated and logged but never
                  passed to the adapter even if one is provided.
    """

    #: Outreach templates keyed by a simplified seniority / title category.
    _TEMPLATES: dict[str, str] = {
        "founder": textwrap.dedent("""\
            Hi {first_name},

            I came across {company_name} while researching companies in the \
{industry} space, and I was genuinely impressed by what you've built.

            We work with founders like yourself to {value_prop}. \
Given {company_name}'s focus and scale ({employee_hint}), I think there could \
be a real opportunity to explore together.

            Would you be open to a quick 20-minute chat this week or next?

            Looking forward to connecting,
            [Your Name]
            [Your Company]
        """),
        "executive": textwrap.dedent("""\
            Hi {first_name},

            I hope this note finds you well. I recently discovered \
{company_name} while researching leading companies in \
{industry} and was keen to reach out.

            We help {title_plural} at companies like yours to \
{value_prop}. Given {company_name}'s trajectory ({employee_hint}), I thought \
our work might be particularly relevant.

            Would you have 20 minutes to connect and explore potential synergies?

            Best regards,
            [Your Name]
            [Your Company]
        """),
        "default": textwrap.dedent("""\
            Hi {first_name},

            I reached out because I admire the work {company_name} is doing in \
the {industry} space.

            We help teams {value_prop}, and I believe there is a strong \
alignment with what your team is working on ({employee_hint}).

            I'd love to schedule a quick 20-minute intro call — would any time \
this week or next work for you?

            Warm regards,
            [Your Name]
            [Your Company]
        """),
    }

    def __init__(
        self,
        adapter: OutreachInterface | None = None,
        dry_run: bool = True,
    ) -> None:
        self._adapter = adapter
        self._dry_run = dry_run

    # ── public interface ──────────────────────────────────────────────────────

    def generate_message(self, contact: "Contact", company: "Company") -> str:
        """
        Generate a personalised plain-text outreach message.

        The template is chosen based on the contact's title / seniority.
        All personalisation tokens are filled in from the model fields.

        Args:
            contact: Target contact.
            company: The company the contact works at.

        Returns:
            Plain-text outreach message string.
        """
        template_key = self._select_template(contact)
        template = self._TEMPLATES[template_key]

        first_name = (
            contact.first_name or contact.full_name.split()[0] or "there"
        )
        industry = company.industry or "your industry"
        employee_hint = self._employee_hint(company)
        title_plural = self._title_plural(contact.title)

        message = template.format(
            first_name=first_name,
            company_name=company.name,
            industry=industry,
            employee_hint=employee_hint,
            value_prop="streamline sales and outreach workflows at scale",
            title_plural=title_plural,
        )

        logger.debug(
            "OutreachService: generated message for %s @ %s (%d chars).",
            contact.full_name,
            company.domain,
            len(message),
        )
        return message

    def process(self, contact: "Contact", company: "Company") -> str:
        """
        Generate a message **and** optionally dispatch it via the adapter.

        In dry-run mode (or when no adapter is set), the message is only
        returned and logged — nothing is sent.

        Args:
            contact: Target contact.
            company: The company the contact works at.

        Returns:
            The generated outreach message string.
        """
        message = self.generate_message(contact, company)

        if self._dry_run or self._adapter is None:
            mode = "dry-run" if self._dry_run else "no-adapter"
            logger.info(
                "OutreachService [%s]: message ready for %s (not sent).",
                mode,
                contact.display(),
            )
        else:
            logger.info(
                "OutreachService: dispatching message to %s via adapter …",
                contact.display(),
            )
            self._adapter.send(message, contact)

        return message

    # ── private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _select_template(contact: "Contact") -> str:
        """Choose the right template key based on title / seniority."""
        title_lower = (contact.title or "").lower()
        seniority_lower = (contact.seniority or "").lower()

        founder_keywords = {"founder", "co-founder", "cofounder"}
        executive_keywords = {
            "ceo", "cto", "coo", "cmo", "cfo",
            "vp", "vice president", "director", "head of",
            "president", "c_suite",
        }

        if any(k in title_lower for k in founder_keywords):
            return "founder"
        if any(k in title_lower or k in seniority_lower for k in executive_keywords):
            return "executive"
        return "default"

    @staticmethod
    def _employee_hint(company: "Company") -> str:
        """Return a human-readable employee count hint."""
        if not company.employee_count:
            return "your scale"
        count = company.employee_count
        if count < 50:
            return f"a team of ~{count}"
        if count < 500:
            return f"~{count} employees"
        return f"~{count:,} employees globally"

    @staticmethod
    def _title_plural(title: str | None) -> str:
        """Convert a singular title to a rough plural for template copy."""
        if not title:
            return "leaders"
        lower = title.lower()
        if lower.startswith("vp"):
            return f"{title}s"
        if lower in ("ceo", "cto", "coo", "cmo", "cfo"):
            return f"{title}s"
        return f"{title}s"
