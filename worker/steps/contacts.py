"""Contacts step: who to reach at each researched company, and how.

Two sources:
- website: emails and tel: links on the pages we already crawled (free, reliable)
- search: one Gemini + Google Search call per company for named decision-makers

Search results are verified by fetching source_url and checking the email / phone / name
actually appears there. Only business contact details published publicly are collected.
"""

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from worker import llm
from worker.config import CONTACTS_PER_COMPANY, MODEL, PARALLEL_FETCHES
from worker.crawl import CrawlResult, fetch_html, fetch_html_quick
from worker.discovery.domains import normalize_domain
from worker.schemas import Candidate, Contact
from worker.verify import TEL_RE, digits, email_on_page, name_on_page, phone_on_page, strip_code

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
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
        html = strip_code(html)  # emails inside scripts are config/tracking, not contacts
        for email in dict.fromkeys(e.lower() for e in EMAIL_RE.findall(html)):
            host = email.split("@")[1]
            if (host == company or host.endswith("." + company)) and not JUNK_EMAIL.search(email):
                out.append(Contact(role=_inbox_role(email), email=email, source_url=page.url,
                                   origin="website", verified=True))
        for tel in dict.fromkeys(t.strip() for t in TEL_RE.findall(html)):
            if is_placeholder_phone(tel):
                continue
            out.append(Contact(role="Phone (website)", phone=tel, source_url=page.url,
                               origin="website", verified=True))
    return out


def is_placeholder_phone(phone: str) -> bool:
    """Template numbers like +1234567890, 000-000-0000 or 555-5555 left in page markup."""
    d = digits(phone)
    return (
        len(d) < 7
        or len(set(d)) <= 2
        or d.lstrip("0") in "1234567890" * 2
        or d.lstrip("1").startswith("5555")
    )


def verify(c: Contact, html_for: Callable[[str], str] | None = None) -> bool:
    """Does the email / phone (or, failing those, the name) appear on source_url?"""
    html_for = html_for or fetch_html_quick
    try:
        html = html_for(c.source_url)
    except Exception:  # unreachable (or LinkedIn blocking us) = can't verify, not "false"
        return False
    if c.email:
        return email_on_page(c.email, html)
    if c.phone:
        return phone_on_page(c.phone, html)
    return bool(c.name) and name_on_page(c.name, html)


def from_search(candidate: Candidate, verifier: Callable[[Contact], bool] | None = None) -> list[Contact]:
    verifier = verifier or verify
    prompt = PROMPT.format(name=candidate.name, website=candidate.website, n=CONTACTS_PER_COMPANY)
    found = llm.ask(prompt, FoundContacts, model=MODEL, use_search=True).contacts
    out = []
    for f in found[:CONTACTS_PER_COMPANY]:
        if not (f.name or f.email or f.phone):
            continue
        email = f.email.strip().lower() if f.email and EMAIL_RE.fullmatch(f.email.strip()) else None
        phone = f.phone if f.phone and not is_placeholder_phone(f.phone) else None
        if not (f.name or email or phone):
            continue
        out.append(Contact(**{**f.model_dump(), "email": email, "phone": phone}, origin="search"))
    with ThreadPoolExecutor(PARALLEL_FETCHES) as pool:  # each check fetches a page
        for c, ok in zip(out, pool.map(verifier, out)):
            c.verified = ok
    return out


def _key(c: Contact) -> str:
    return (c.email or digits(c.phone or "") or (c.name or "")).lower()


def _combine(a: Contact, b: Contact) -> Contact:
    """One contact from two records of the same email/phone/name, whatever order they come in.

    The verified record supplies source and origin; name and role come from whichever record
    names a person; other gaps are filled from either.
    """
    primary, other = (a, b) if (a.verified, bool(a.name)) >= (b.verified, bool(b.name)) else (b, a)
    named = primary if primary.name or not other.name else other
    merged = {f: getattr(primary, f) or getattr(other, f) for f in ("email", "phone", "linkedin_url")}
    return primary.model_copy(update={
        **merged,
        "name": named.name,
        "role": named.role or primary.role or other.role,
        "verified": a.verified or b.verified,
    })


def merge_contacts(contacts: list[Contact]) -> list[Contact]:
    """Dedupe by email / phone / name; verified people with email first."""
    by_key: dict[str, Contact] = {}
    for c in contacts:
        k = _key(c)
        if k:
            by_key[k] = _combine(by_key[k], c) if k in by_key else c
    return sorted(by_key.values(), key=lambda c: (not c.verified, not c.name, not c.email))


def find_contacts(candidate: Candidate, crawl: CrawlResult) -> list[Contact]:
    return merge_contacts(from_website(crawl) + from_search(candidate))
