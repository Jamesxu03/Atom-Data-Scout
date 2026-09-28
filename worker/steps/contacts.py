"""Contacts step: who to reach at each profiled company, and how.

Two sources:
- website: emails and tel: links on the pages we already crawled (free, reliable)
- search: one Gemini + Google Search call per company for named decision-makers

Search results are verified by fetching source_url and checking the email / phone / name
actually appears there. Only business contact details published publicly are collected.
"""

import re
from collections.abc import Callable

from pydantic import BaseModel

from worker import llm
from worker.config import CONTACTS_PER_COMPANY, MODEL_FLASH
from worker.crawl import CrawlResult, fetch_html, fetch_html_quick
from worker.discovery.domains import normalize_domain
from worker.schemas import Candidate, Contact

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TEL_RE = re.compile(r'href=["\']tel:([^"\']+)["\']', re.I)
JUNK_EMAIL = re.compile(
    r"\.(png|jpe?g|gif|svg|webp)$|@(example\.|sentry|wixpress|domain\.com|email\.com)", re.I
)
INBOX_ROLES = {
    ("privacy", "dpo", "dataprotection", "gdpr"): "Privacy / DPO inbox",
    ("partnerships", "partners", "bd", "business", "sales", "marketing"): "Sales / partnerships inbox",
    ("legal", "copyright"): "Legal inbox",
    ("press", "media", "pr"): "Press inbox",
    ("info", "hello", "contact", "enquiries", "hi"): "General inbox",
    ("support", "help"): "Support inbox",
}


class FoundContact(BaseModel):
    name: str | None
    role: str | None
    email: str | None
    phone: str | None
    linkedin_url: str | None
    source_url: str


class FoundContacts(BaseModel):
    contacts: list[FoundContact]


PROMPT = """Find people to contact at {name} ({website}) about licensing or buying data the
company holds. Use Google Search.

Look for, in this order of usefulness:
- founders, CEO, CTO, COO
- heads of data, AI/ML, product, partnerships, business development or corporate development
- the data protection officer (DPO)
- if the company is insolvent, in administration or liquidation: the appointed administrators
  or liquidators (insolvency practitioners) handling it

Rules:
- Only business contact details published publicly in a professional capacity: the company
  website, press releases, official filings, insolvency notices, conference pages, or the
  person's professional profile. No personal home addresses or personal phone numbers.
- Never guess or construct an email from a naming pattern (e.g. firstname@company.com).
  If no email is published, leave email null — a name, role and source is still useful.
- source_url: the exact page where the details appear.
- Return at most {n} contacts. Return none rather than invent any.
"""


def _inbox_role(email: str) -> str | None:
    local = email.split("@")[0].lower()
    for prefixes, role in INBOX_ROLES.items():
        if local.startswith(prefixes):
            return role
    return None


def from_website(crawl: CrawlResult, html_for: Callable[[str], str] | None = None) -> list[Contact]:
    """Emails and tel: links on the crawled pages. Always verified (we read them ourselves).

    Only emails on the company's own domain: others are form vendors, placeholders or examples.
    """
    html_for = html_for or fetch_html
    company = normalize_domain(crawl.website) or ""
    out = []
    for page in crawl.pages():
        try:
            html = html_for(page.url)
        except Exception:  # page came from cache during crawl; a miss here isn't worth failing on
            continue
        for email in dict.fromkeys(e.lower() for e in EMAIL_RE.findall(html)):
            host = email.split("@")[1]
            if (host == company or host.endswith("." + company)) and not JUNK_EMAIL.search(email):
                out.append(Contact(role=_inbox_role(email), email=email, source_url=page.url,
                                   origin="website", verified=True))
        for tel in dict.fromkeys(t.strip() for t in TEL_RE.findall(html)):
            out.append(Contact(role="Phone (website)", phone=tel, source_url=page.url,
                               origin="website", verified=True))
    return out


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def verify(c: Contact, html_for: Callable[[str], str] | None = None) -> bool:
    """Does the email / phone (or, failing those, the name) appear on source_url?"""
    html_for = html_for or fetch_html_quick
    try:
        html = html_for(c.source_url).lower()
    except Exception:  # unreachable (or LinkedIn blocking us) = can't verify, not "false"
        return False
    if c.email:
        return c.email.lower() in html
    if c.phone:
        digits = _digits(c.phone)[-9:]  # ignore country-code formatting differences
        return len(digits) >= 7 and digits in _digits(html)
    return bool(c.name) and c.name.lower() in html


def from_search(candidate: Candidate, verifier: Callable[[Contact], bool] | None = None) -> list[Contact]:
    verifier = verifier or verify
    prompt = PROMPT.format(name=candidate.name, website=candidate.website, n=CONTACTS_PER_COMPANY)
    found = llm.ask(prompt, FoundContacts, model=MODEL_FLASH, use_search=True).contacts
    out = []
    for f in found[:CONTACTS_PER_COMPANY]:
        if not (f.name or f.email or f.phone):
            continue
        email = f.email.strip().lower() if f.email and EMAIL_RE.fullmatch(f.email.strip()) else None
        c = Contact(**{**f.model_dump(), "email": email}, origin="search")
        c.verified = verifier(c)
        out.append(c)
    return out


def _key(c: Contact) -> str:
    return (c.email or _digits(c.phone or "") or (c.name or "")).lower()


def merge_contacts(contacts: list[Contact]) -> list[Contact]:
    """Dedupe (a named search hit beats a bare website email); verified people with email first."""
    by_key: dict[str, Contact] = {}
    for c in contacts:
        k = _key(c)
        if not k:
            continue
        old = by_key.get(k)
        if old is None or (c.name and not old.name) or (c.verified and not old.verified):
            by_key[k] = c.model_copy(update={"verified": c.verified or (old.verified if old else False)})
    return sorted(by_key.values(), key=lambda c: (not c.verified, not c.name, not c.email))


def find_contacts(candidate: Candidate, crawl: CrawlResult, use_search: bool = True) -> list[Contact]:
    contacts = from_website(crawl)
    if use_search:
        contacts += from_search(candidate)
    return merge_contacts(contacts)
