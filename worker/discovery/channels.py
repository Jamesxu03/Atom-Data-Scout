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


def check_source_page(url: str) -> tuple[str, str | None, bool]:
    """(real URL, page HTML or None, grounded) for one source URL — fetched once, however many
    companies were found on it.

    The search response doesn't list the pages it used, so we check directly: a grounding
    redirect only resolves if Google issued it (grounded=True, no fetch needed); any other
    URL the model wrote itself is fetched so callers can check it mentions the company.
    """
    if GROUNDING_REDIRECT in url:
        resolved = resolve_source(url)
        return resolved, None, resolved != url
    try:
        return url, fetch_html_quick(url), False
    except Exception:  # dead or blocked page: can't confirm the company was listed there
        return url, None, False


def verify_source(found: Found, page: tuple[str, str | None, bool] | None = None) -> tuple[str, bool]:
    """(real source URL, verified) for one company. Pass `page` to reuse a fetched page."""
    url, html, grounded = page or check_source_page(found.source_url)
    return url, grounded or (html is not None and _mentions(html, found.name, found.website))


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

Only use a category that is specific to this type of product. If the closest categories
are broad (e.g. "augmented reality software", "restaurant management", "warehouse
software"), return only vendors whose listing clearly describes this type of product, or
none at all.

Return the vendors listed in those categories. source_url must be the directory page
where each vendor is listed.""")


# (d) One call per angle, each searching a different place data vendors show up. Dataset
# language is right here, unlike in product search.
MARKETPLACE_ANGLES = {
    "marketplace listings": """Search data marketplaces such as Datarade, AWS Data Exchange,
Snowflake Marketplace and Defined.ai for listings of this kind of data, e.g.
"{data_type} dataset". Return the company that provides each listing, not the marketplace.
source_url must be the listing page.""",
    "collection services": """Search for data collection and annotation companies that
record this kind of data to order or already hold it, e.g. "{data_type} data collection
service", "{data_type} training data provider", "hours of {data_type} for AI training".
source_url must be the company's page describing this service or data.""",
    "published datasets": """Search Hugging Face, Kaggle, GitHub, arXiv and news or press
releases for datasets of this kind that companies have released or announced, e.g.
"{data_type} dataset hugging face", "startup releases {data_type} dataset". Return the
company that recorded the data, not the hosting site. source_url must be the dataset page,
paper or article.""",
}


def marketplace(spec: DataSpec, angle: str) -> list[Found]:
    """(d) Companies that already produce or sell this kind of data, searched from one angle."""
    return _search(f"""Find companies that already produce or sell data similar to this:
- data type: {spec.data_type}
- scenarios: {", ".join(spec.scenarios) or "any"}
- languages: {", ".join(spec.languages) or "any"}
- labels: {", ".join(spec.label_types) or "none"}

{MARKETPLACE_ANGLES[angle].format(data_type=spec.data_type)}

Also search with the terms data vendors and researchers use for this kind of data, which
may differ from the wording above (e.g. "egocentric" for first-person video).""")
