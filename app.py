"""
app.py
------
Streamlit UI for the Automated Outreach Pipeline.

Reuses the existing backend exactly as-is:
  - OceanService     (company discovery)
  - ProspeoService   (contact enrichment)
  - OutreachService  (message generation)
  - Database         (SQLite persistence)
  - Config / get_config
  - models.Company / models.Contact
  - exceptions.*

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import io
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import streamlit as st

# ── page config (must be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="Outreach Pipeline",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── project imports ───────────────────────────────────────────────────────────
from config import get_config
from db.database import Database
from exceptions import (
    AuthenticationError,
    PipelineError,
    RateLimitError,
)
from models.company import Company
from models.contact import Contact
from services.ocean_service import OceanService
from services.outreach_service import OutreachService
from services.prospeo_service import ProspeoService
from utils.validators import validate_domain

# ── styling — white + yellow theme, no emojis ────────────────────────────────
_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
    background-color: #ffffff;
    color: #1a1a1a;
}

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background-color: #fafafa;
    border-right: 1px solid #e8e8e8;
}
[data-testid="stSidebar"] * {
    color: #1a1a1a !important;
}
[data-testid="stSidebar"] .stSlider label,
[data-testid="stSidebar"] .stCheckbox label {
    color: #444444 !important;
    font-size: 0.85rem;
}

/* ── App header ── */
.app-title {
    font-size: 1.75rem;
    font-weight: 700;
    color: #1a1a1a;
    letter-spacing: -0.5px;
    margin-bottom: 2px;
}
.app-title span {
    color: #f5a623;
}
.app-subtitle {
    font-size: 0.88rem;
    color: #666666;
    margin-top: 0;
}

/* ── Sidebar brand ── */
.sidebar-brand {
    font-size: 1.1rem;
    font-weight: 700;
    color: #1a1a1a !important;
    letter-spacing: -0.3px;
}
.sidebar-brand span {
    color: #f5a623;
}
.sidebar-tagline {
    font-size: 0.75rem;
    color: #888888 !important;
    margin-top: 2px;
}

/* ── Run button ── */
div.stButton > button[kind="primary"] {
    background-color: #f5a623;
    border: none;
    border-radius: 6px;
    color: #1a1a1a;
    font-weight: 600;
    font-size: 0.9rem;
    padding: 0.55rem 1.5rem;
    width: 100%;
    transition: background-color 0.2s ease;
    box-shadow: 0 2px 6px rgba(245,166,35,0.3);
}
div.stButton > button[kind="primary"]:hover {
    background-color: #e09510;
}
div.stButton > button[kind="primary"]:disabled {
    background-color: #e8e8e8;
    color: #aaaaaa;
    box-shadow: none;
}

/* ── Metric cards ── */
[data-testid="stMetric"] {
    background-color: #fffdf5;
    border: 1px solid #f0e8d0;
    border-radius: 8px;
    padding: 1rem 1.25rem;
}
[data-testid="stMetricValue"] {
    color: #1a1a1a !important;
    font-weight: 700;
    font-size: 1.6rem !important;
}
[data-testid="stMetricLabel"] {
    color: #666666 !important;
    font-size: 0.78rem !important;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}

/* ── Status badges ── */
.badge {
    display: inline-block;
    padding: 2px 9px;
    border-radius: 4px;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.03em;
}
.badge-success  { background: #e6f9f0; color: #187a47; border: 1px solid #b8e8cc; }
.badge-limited  { background: #fff8e6; color: #b07000; border: 1px solid #f0d080; }
.badge-failed   { background: #fef0f0; color: #b02020; border: 1px solid #f0b8b8; }
.badge-skipped  { background: #f4f4f4; color: #666666; border: 1px solid #d8d8d8; }

/* ── Section headings ── */
.section-header {
    font-size: 0.95rem;
    font-weight: 600;
    color: #1a1a1a;
    border-bottom: 2px solid #f5a623;
    padding-bottom: 5px;
    margin-bottom: 14px;
    display: inline-block;
}

/* ── Message box ── */
.message-box {
    background: #fffdf7;
    border-left: 3px solid #f5a623;
    border-radius: 0 6px 6px 0;
    padding: 14px 18px;
    font-family: 'Inter', sans-serif;
    font-size: 0.85rem;
    color: #333333;
    white-space: pre-wrap;
    line-height: 1.65;
    border: 1px solid #f0e8d0;
    border-left: 3px solid #f5a623;
}

/* ── Tables ── */
[data-testid="stDataFrame"] {
    border-radius: 8px;
    border: 1px solid #e8e8e8;
    overflow: hidden;
}

/* ── Download buttons ── */
div.stDownloadButton > button {
    border-radius: 6px;
    font-size: 0.82rem;
    border: 1px solid #d0d0d0;
    color: #333333;
    background-color: #ffffff;
    transition: border-color 0.2s;
}
div.stDownloadButton > button:hover {
    border-color: #f5a623;
    color: #b07000;
}

/* ── Divider ── */
hr {
    border: none;
    border-top: 1px solid #eeeeee;
    margin: 16px 0;
}

/* ── Info / success / error ── */
[data-testid="stAlert"] {
    border-radius: 6px;
}

/* ── Expander ── */
[data-testid="stExpander"] {
    border: 1px solid #e8e8e8;
    border-radius: 6px;
}
[data-testid="stExpander"] summary {
    font-size: 0.88rem;
    font-weight: 500;
}

/* ── Config status badge in sidebar ── */
.config-ok {
    background: #e6f9f0;
    color: #187a47;
    border: 1px solid #b8e8cc;
    border-radius: 4px;
    padding: 4px 10px;
    font-size: 0.78rem;
    font-weight: 600;
    display: inline-block;
}
.config-err {
    background: #fef0f0;
    color: #b02020;
    border: 1px solid #f0b8b8;
    border-radius: 4px;
    padding: 4px 10px;
    font-size: 0.78rem;
    font-weight: 600;
    display: inline-block;
}
</style>
"""
st.markdown(_CSS, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Session State
# ══════════════════════════════════════════════════════════════════════════════

def _init_state() -> None:
    defaults: dict[str, Any] = {
        "run_result": None,
        "companies": [],
        "contacts_by_domain": {},
        "run_id": None,
        "run_duration": None,
        "running": False,
        "error": None,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

_init_state()


# ══════════════════════════════════════════════════════════════════════════════
# Config check
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_resource(show_spinner=False)
def _load_config():
    try:
        return get_config(), None
    except EnvironmentError as exc:
        return None, str(exc)

_config, _config_err = _load_config()


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str).encode()


def _companies_csv_bytes(companies: list[Company]) -> bytes:
    buf = io.StringIO()
    import csv
    writer = csv.DictWriter(buf, fieldnames=[
        "domain", "name", "industry", "employee_count",
        "location", "similarity_score", "contacts_fetch_status",
    ])
    writer.writeheader()
    for c in companies:
        writer.writerow({
            "domain": c.domain,
            "name": c.name,
            "industry": c.industry or "",
            "employee_count": c.employee_count or "",
            "location": c.location or "",
            "similarity_score": c.similarity_score or "",
            "contacts_fetch_status": c.contacts_fetch_status or "",
        })
    return buf.getvalue().encode()


def _contacts_csv_bytes(contacts_by_domain: dict[str, list[Contact]]) -> bytes:
    buf = io.StringIO()
    import csv
    writer = csv.DictWriter(buf, fieldnames=[
        "full_name", "title", "email", "linkedin_url",
        "seniority", "company_domain",
    ])
    writer.writeheader()
    for contacts in contacts_by_domain.values():
        for c in contacts:
            writer.writerow({
                "full_name": c.full_name,
                "title": c.title or "",
                "email": c.email or "",
                "linkedin_url": c.linkedin_url or "",
                "seniority": c.seniority or "",
                "company_domain": c.company_domain,
            })
    return buf.getvalue().encode()


# ══════════════════════════════════════════════════════════════════════════════
# Pipeline execution
# ══════════════════════════════════════════════════════════════════════════════

def _run_pipeline(
    domain: str,
    max_companies: int,
    max_contacts: int,
    dry_run: bool,
    ocean_only: bool,
    progress_cb,
    status_area,
) -> None:
    run_id = str(uuid.uuid4())
    t_start = time.monotonic()

    st.session_state.run_id = run_id
    st.session_state.error = None

    companies: list[Company] = []
    contacts_by_domain: dict[str, list[Contact]] = {}

    # Step 1 — validate domain
    progress_cb(0.05, "Validating domain...")
    try:
        domain = validate_domain(domain)
    except PipelineError as exc:
        st.session_state.error = f"Invalid domain: {exc}"
        return

    # Step 2 — Ocean.io lookalike search
    progress_cb(0.15, f"Searching Ocean.io for companies similar to {domain}...")
    try:
        with OceanService(_config) as ocean:
            companies = ocean.find_similar_companies(domain, limit=max_companies)
    except PipelineError as exc:
        st.session_state.error = f"Ocean.io error: {exc}"
        return

    if not companies:
        st.session_state.error = "No similar companies found. Try a different seed domain."
        return

    progress_cb(0.40, f"Found {len(companies)} companies. Fetching contacts...")

    # Step 3 — Prospeo contact fetch
    if ocean_only:
        for company in companies:
            object.__setattr__(company, "contacts_fetch_status", "skipped")
            contacts_by_domain[company.domain] = []
        progress_cb(0.75, "Skipped Prospeo (ocean-only mode).")
    else:
        outreach_svc = OutreachService(dry_run=dry_run)
        rate_limit_triggered = False

        with ProspeoService(_config) as prospeo:
            n = len(companies)
            for i, company in enumerate(companies):
                frac = 0.40 + 0.45 * ((i + 1) / n)

                if rate_limit_triggered:
                    status_area.markdown(
                        f"Rate limited — marking **{company.domain}** as rate_limited."
                    )
                    object.__setattr__(company, "contacts_fetch_status", "rate_limited")
                    contacts_by_domain[company.domain] = []
                    continue

                progress_cb(
                    frac,
                    f"Fetching contacts at {company.domain} ({i+1}/{n})..."
                )

                try:
                    contacts = prospeo.find_contacts(company.domain, limit=max_contacts)

                    for contact in contacts:
                        try:
                            msg = outreach_svc.process(contact, company)
                            object.__setattr__(contact, "outreach_message", msg)
                        except Exception:
                            pass

                    contacts_by_domain[company.domain] = contacts
                    object.__setattr__(company, "contacts_fetch_status", "success")

                except RateLimitError:
                    status_area.markdown(
                        f"Prospeo rate-limited at **{company.domain}** — continuing..."
                    )
                    contacts_by_domain[company.domain] = []
                    object.__setattr__(company, "contacts_fetch_status", "rate_limited")
                    rate_limit_triggered = True

                except AuthenticationError as exc:
                    st.session_state.error = f"Prospeo authentication failed: {exc}"
                    return

                except PipelineError:
                    contacts_by_domain[company.domain] = []
                    object.__setattr__(company, "contacts_fetch_status", "failed")

    # Step 4 — persist
    progress_cb(0.90, "Saving to database...")
    total_contacts = sum(len(v) for v in contacts_by_domain.values())
    timestamp = datetime.now(timezone.utc).isoformat()

    company_dicts = []
    for c in companies:
        cd = json.loads(c.model_dump_json())
        cd["contacts"] = [
            json.loads(ct.model_dump_json())
            for ct in contacts_by_domain.get(c.domain, [])
        ]
        company_dicts.append(cd)

    fetch_summary = {
        "success":      sum(1 for c in companies if c.contacts_fetch_status == "success"),
        "rate_limited": sum(1 for c in companies if c.contacts_fetch_status == "rate_limited"),
        "failed":       sum(1 for c in companies if c.contacts_fetch_status == "failed"),
        "skipped":      sum(1 for c in companies if c.contacts_fetch_status == "skipped"),
    }

    result: dict[str, Any] = {
        "run_id": run_id,
        "source_domain": domain,
        "timestamp": timestamp,
        "dry_run": dry_run,
        "ocean_only": ocean_only,
        "companies_found": len(companies),
        "total_contacts_found": total_contacts,
        "contacts_fetch_summary": fetch_summary,
        "companies": company_dicts,
    }

    try:
        with Database() as db:
            db.save_run(
                run_id=run_id,
                source_domain=domain,
                timestamp=timestamp,
                dry_run=dry_run,
                ocean_only=ocean_only,
                companies_found=len(companies),
                contacts_found=total_contacts,
            )
            db.save_companies(run_id, companies)
            db.save_contacts(run_id, contacts_by_domain)
    except Exception:
        pass

    duration = time.monotonic() - t_start
    st.session_state.run_result = result
    st.session_state.companies = companies
    st.session_state.contacts_by_domain = contacts_by_domain
    st.session_state.run_duration = duration

    progress_cb(1.0, "Done.")


# ══════════════════════════════════════════════════════════════════════════════
# Sidebar
# ══════════════════════════════════════════════════════════════════════════════

def _render_sidebar() -> dict[str, Any]:
    with st.sidebar:
        st.markdown(
            "<p class='sidebar-brand'>Outreach <span>Pipeline</span></p>"
            "<p class='sidebar-tagline'>Powered by Ocean.io + Prospeo</p>",
            unsafe_allow_html=True,
        )
        st.divider()

        if _config_err:
            st.markdown(
                "<span class='config-err'>API keys missing</span>",
                unsafe_allow_html=True,
            )
            st.caption(_config_err)
        else:
            st.markdown(
                "<span class='config-ok'>API keys loaded</span>",
                unsafe_allow_html=True,
            )

        st.divider()
        st.markdown("**Pipeline Settings**")

        domain = st.text_input(
            "Seed Domain",
            value="notion.so",
            placeholder="e.g. notion.so",
            help="Find lookalike companies for this domain.",
        )

        max_companies = st.slider(
            "Max Companies",
            min_value=1, max_value=50, value=10, step=1,
            help="Maximum lookalike companies from Ocean.io.",
        )

        max_contacts = st.slider(
            "Max Contacts per Company",
            min_value=1, max_value=20, value=5, step=1,
            help="Maximum decision-maker contacts per company.",
        )

        st.divider()
        st.markdown("**Mode**")

        dry_run = st.checkbox(
            "Dry Run",
            value=True,
            help="Generate outreach messages but do not send emails.",
        )
        ocean_only = st.checkbox(
            "Ocean Only",
            value=False,
            help="Fetch companies only — skip Prospeo contact lookup.",
        )

        st.divider()

        run_disabled = bool(_config_err) or st.session_state.running
        run_clicked = st.button(
            "Run Pipeline",
            type="primary",
            disabled=run_disabled,
            use_container_width=True,
        )

        if st.session_state.run_result:
            st.divider()
            st.markdown("**Last Run**")
            r = st.session_state.run_result
            st.caption(f"ID: {r['run_id'][:8]}...")
            st.caption(f"Time: {r['timestamp'][:19].replace('T', ' ')} UTC")
            if st.session_state.run_duration:
                st.caption(f"Duration: {st.session_state.run_duration:.1f}s")

    return {
        "domain": domain,
        "max_companies": max_companies,
        "max_contacts": max_contacts,
        "dry_run": dry_run,
        "ocean_only": ocean_only,
        "run_clicked": run_clicked,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Results rendering
# ══════════════════════════════════════════════════════════════════════════════

def _render_stats(result: dict[str, Any]) -> None:
    st.markdown(
        '<p class="section-header">Run Statistics</p>',
        unsafe_allow_html=True,
    )
    summary = result.get("contacts_fetch_summary", {})
    duration = st.session_state.run_duration

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Companies Found",   result["companies_found"])
    c2.metric("Contacts Found",    result["total_contacts_found"])
    c3.metric("Run Duration",      f"{duration:.1f}s" if duration else "-")
    c4.metric("Success",           summary.get("success", 0))
    c5.metric("Rate Limited",      summary.get("rate_limited", 0))
    c6.metric("Failed",            summary.get("failed", 0))


def _render_companies_table(companies: list[Company]) -> None:
    st.markdown(
        '<p class="section-header">Similar Companies</p>',
        unsafe_allow_html=True,
    )
    if not companies:
        st.info("No companies found.")
        return

    import pandas as pd

    rows = []
    for c in companies:
        rows.append({
            "Company Name":  c.name,
            "Domain":        c.domain,
            "Industry":      c.industry or "-",
            "Employees":     c.employee_count or "-",
            "Location":      c.location or "-",
            "Score":         f"{int((c.similarity_score or 0) * 100)}%",
            "Status":        c.contacts_fetch_status or "-",
        })

    df = pd.DataFrame(rows)

    def _colour_status(val: str) -> str:
        return {
            "success":      "color:#187a47; font-weight:600",
            "rate_limited": "color:#b07000; font-weight:600",
            "failed":       "color:#b02020; font-weight:600",
            "skipped":      "color:#666666",
        }.get(val, "")

    styled = df.style.map(_colour_status, subset=["Status"])
    st.dataframe(styled, use_container_width=True, hide_index=True)


def _render_contacts_table(contacts_by_domain: dict[str, list[Contact]]) -> None:
    st.markdown(
        '<p class="section-header">Contacts</p>',
        unsafe_allow_html=True,
    )

    all_contacts = [c for v in contacts_by_domain.values() for c in v]

    if not all_contacts:
        st.info(
            "No contacts fetched. Run without Ocean Only mode, "
            "or wait for the Prospeo rate limit to reset."
        )
        return

    import pandas as pd

    rows = [{
        "Name":      c.full_name,
        "Title":     c.title or "-",
        "Email":     c.email or "-",
        "Company":   c.company_domain,
        "LinkedIn":  c.linkedin_url or "-",
        "Seniority": c.seniority or "-",
    } for c in all_contacts]

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_outreach_messages(
    companies: list[Company],
    contacts_by_domain: dict[str, list[Contact]],
) -> None:
    st.markdown(
        '<p class="section-header">Outreach Messages</p>',
        unsafe_allow_html=True,
    )

    any_message = False
    for company in companies:
        contacts_with_msg = [
            c for c in contacts_by_domain.get(company.domain, [])
            if c.outreach_message
        ]
        if not contacts_with_msg:
            continue

        any_message = True
        for contact in contacts_with_msg:
            label = f"{contact.full_name} — {contact.title or 'Contact'} at {company.name}"
            with st.expander(label, expanded=False):
                col_left, col_right = st.columns([3, 1])
                with col_left:
                    st.markdown(
                        f'<div class="message-box">{contact.outreach_message}</div>',
                        unsafe_allow_html=True,
                    )
                with col_right:
                    st.markdown("**Contact Details**")
                    if contact.email:
                        st.caption(f"Email: {contact.email}")
                    if contact.linkedin_url:
                        st.markdown(f"[View LinkedIn]({contact.linkedin_url})")
                    st.caption(f"Company: {company.name}")
                    if company.industry:
                        st.caption(f"Industry: {company.industry}")

    if not any_message:
        st.info(
            "No outreach messages generated yet. "
            "Run the pipeline with contacts enabled."
        )


def _render_downloads(
    result: dict[str, Any],
    companies: list[Company],
    contacts_by_domain: dict[str, list[Contact]],
) -> None:
    st.markdown(
        '<p class="section-header">Downloads</p>',
        unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns(3)

    with c1:
        st.download_button(
            label="Download JSON",
            data=_json_bytes(result),
            file_name=f"pipeline_{result['run_id'][:8]}.json",
            mime="application/json",
            use_container_width=True,
        )
    with c2:
        st.download_button(
            label="Download Companies CSV",
            data=_companies_csv_bytes(companies),
            file_name=f"companies_{result['run_id'][:8]}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with c3:
        all_contacts = [c for v in contacts_by_domain.values() for c in v]
        st.download_button(
            label="Download Contacts CSV",
            data=_contacts_csv_bytes(contacts_by_domain),
            file_name=f"contacts_{result['run_id'][:8]}.csv",
            mime="text/csv",
            use_container_width=True,
            disabled=not all_contacts,
        )


def _render_status_legend() -> None:
    badges = "&nbsp;&nbsp;&nbsp;".join([
        '<span class="badge badge-success">success</span>',
        '<span class="badge badge-limited">rate limited</span>',
        '<span class="badge badge-failed">failed</span>',
        '<span class="badge badge-skipped">skipped</span>',
    ])
    st.markdown(
        f'<div style="margin-bottom:14px;font-size:0.8rem;color:#666">'
        f'Status: &nbsp;{badges}</div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Main layout
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    ctrl = _render_sidebar()

    # Page header
    st.markdown(
        "<p class='app-title'>Automated <span>Outreach</span> Pipeline</p>"
        "<p class='app-subtitle'>"
        "Discover lookalike companies &middot; Find decision-makers "
        "&middot; Generate personalised outreach"
        "</p>",
        unsafe_allow_html=True,
    )

    # Config error
    if _config_err:
        st.error(
            f"Configuration Error: cannot run pipeline.\n\n"
            f"{_config_err}\n\n"
            "Edit your .env file and restart Streamlit."
        )
        return

    # Pipeline execution
    if ctrl["run_clicked"]:
        st.session_state.running = True

        progress_bar = st.progress(0.0, text="Starting...")
        status_area  = st.empty()

        def _progress(frac: float, msg: str) -> None:
            progress_bar.progress(frac, text=msg)
            status_area.markdown(msg)

        try:
            _run_pipeline(
                domain=ctrl["domain"],
                max_companies=ctrl["max_companies"],
                max_contacts=ctrl["max_contacts"],
                dry_run=ctrl["dry_run"],
                ocean_only=ctrl["ocean_only"],
                progress_cb=_progress,
                status_area=status_area,
            )
        finally:
            st.session_state.running = False
            progress_bar.empty()
            status_area.empty()

        if st.session_state.error:
            st.error(st.session_state.error)
        else:
            r = st.session_state.run_result
            st.success(
                f"Pipeline complete in {st.session_state.run_duration:.1f}s — "
                f"{r['companies_found']} companies, "
                f"{r['total_contacts_found']} contacts."
            )

    elif st.session_state.error:
        st.error(st.session_state.error)

    # Results
    result            = st.session_state.run_result
    companies         = st.session_state.companies
    contacts_by_domain = st.session_state.contacts_by_domain

    if result:
        st.divider()
        _render_status_legend()
        _render_stats(result)
        st.divider()

        left, right = st.columns([3, 2])
        with left:
            _render_companies_table(companies)
        with right:
            _render_contacts_table(contacts_by_domain)

        st.divider()
        _render_outreach_messages(companies, contacts_by_domain)
        st.divider()
        _render_downloads(result, companies, contacts_by_domain)

    else:
        st.markdown(
            "<div style='text-align:center;padding:60px 20px;color:#999'>"
            "<div style='font-size:2.5rem;font-weight:300;color:#cccccc'>—</div>"
            "<p style='font-size:1rem;font-weight:500;color:#555;margin-top:12px'>"
            "Ready to run</p>"
            "<p style='font-size:0.85rem;color:#999'>"
            "Enter a seed domain in the sidebar and click <strong>Run Pipeline</strong>."
            "</p>"
            "</div>",
            unsafe_allow_html=True,
        )


if __name__ == "__main__":
    main()
