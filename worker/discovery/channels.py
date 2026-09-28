"""Discovery channels. Each is one Gemini call with Google Search returning companies + source URLs."""

import re

import httpx

from worker import llm
from worker.config import DISCOVERY, MODEL
from worker.crawl import fetch_html_quick
from worker.discovery.domains import normalize_domain
from worker.schemas import DataSpec, Found, FoundList, HolderHypothesis

RULES = """Rules:
- Use Google Search. Only return companies you actually found in search results.
- Never invent companies or URLs. If you find fewer than {n}, return fewer.
- website: the company's own homepage (e.g. https://example.com), not the page you found it on.
- source_url: the exact page where you found the company.
- Do not return directories, marketplaces, review sites, news sites or social networks as
  companies — only the businesses listed on them.
- Return at most {n} companies.
"""


GROUNDING_REDIRECT = "vertexaisearch.cloud.google.com/grounding-api-redirect/"


def resolve_source(url: str) -> str:
    """Google Search grounding gives opaque, expiring redirect links; swap in the real page URL."""
    if GROUNDING_REDIRECT not in url:
        return url
    try:
        r = httpx.head(url, follow_redirects=False, timeout=5)
        return r.headers.get("location") or url
    except httpx.HTTPError:
        return url


def _mentions(html: str, name: str, website: str) -> bool:
    """Does the page mention the company, by domain name (e.g. 'fireflies') or full name?"""
    text = html.lower()
    domain = normalize_domain(website) or ""
    labels = domain.split(".")
    brand = labels[-2] if len(labels) >= 2 else ""
    if len(brand) >= 3 and brand in text:
        return True
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    return bool(words) and " ".join(words) in re.sub(r"[^a-z0-9 ]", " ", text)


def verify_source(found: Found) -> tuple[str, bool]:
    """(real source URL, verified).

    The search response doesn't list the pages it used, so check directly: a grounding
    redirect only resolves if Google issued it; a URL the model wrote itself must load and
    mention the company.
    """
    url = found.source_url
    if GROUNDING_REDIRECT in url:
        resolved = resolve_source(url)
        return resolved, resolved != url
    try:
        html = fetch_html_quick(url)
    except Exception:  # dead or blocked page: can't confirm the company was listed there
        return url, False
    return url, _mentions(html, found.name, found.website)


def _search(prompt: str) -> list[Found]:
    n = DISCOVERY["companies_per_call"]
    result = llm.ask(prompt + "\n" + RULES.format(n=n), FoundList, model=MODEL, use_search=True)
    return result.companies[:n]


def product_search(h: HolderHypothesis) -> list[Found]:
    """(a) Search in the product language the companies use about themselves."""
    queries = "\n".join(f"- {q}" for q in h.search_queries)
    return _search(f"""Find real companies of this type: {h.category}.
Why they matter: {h.rationale}

Search with these queries (and close variations):
{queries}

Include small, niche, regional and defunct companies, not only market leaders.""")


def snowball(seed_name: str, seed_website: str) -> list[Found]:
    """(b) Find competitors of a known relevant company via comparison pages."""
    return _search(f"""Find companies that compete with {seed_name} ({seed_website}) —
businesses offering a similar product to a similar customer.

Search for pages such as:
- "alternatives to {seed_name}"
- "{seed_name} vs"
- "{seed_name} competitors"
- "best {seed_name} alternatives"

Collect the competing companies named on those pages. Do not return {seed_name} itself.""")


def directory(h: HolderHypothesis) -> list[Found]:
    """(c) Software directory / category pages (G2, Capterra, Product Hunt...)."""
    return _search(f"""Find software directory category pages for this type of product:
{h.category}.

Search G2 (g2.com/categories/...), Capterra, GetApp and Product Hunt for the category that
best matches, e.g. "site:g2.com {h.category}", "site:capterra.com {h.category}",
"site:producthunt.com {h.category}".

Return the vendors listed in those categories. source_url must be the directory page
where each vendor is listed.""")


def marketplace(spec: DataSpec) -> list[Found]:
    """(d) Data marketplaces where similar data is already listed."""
    return _search(f"""Find data providers already offering data similar to this on data
marketplaces:
- data type: {spec.data_type}
- languages: {", ".join(spec.languages) or "any"}
- labels: {", ".join(spec.label_types) or "none"}

Search marketplaces and dataset catalogues such as Datarade, AWS Data Exchange, Snowflake
Marketplace, Defined.ai and Hugging Face for listings of this kind of data (here dataset
language is right, e.g. "{spec.data_type} dataset").

Return the company that provides each listing, not the marketplace. source_url must be the
listing page.""")
