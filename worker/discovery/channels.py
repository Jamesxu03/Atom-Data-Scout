"""Discovery channels. Each is one Gemini call with Google Search returning companies + source URLs."""

import re

import httpx

from worker import archetypes, llm
from worker.brief_template import Brief
from worker.config import DISCOVERY, MODEL
from worker.crawl import fetch_html_quick
from worker.discovery.domains import normalize_domain
from worker.schemas import Found, FoundList, HolderHypothesis

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


_LEGAL_SUFFIXES = {"inc", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "companies",
                   "group", "plc", "gmbh", "sa", "ag", "bv"}


def name_variants(name: str) -> list[str]:
    """The ways a page may write a company's name, lowercased: the name without any bracketed
    part, each bracketed part ("Homepoint (Home Point Capital)"), and each without a legal
    suffix ("Ebby Halliday Companies" -> "ebby halliday")."""
    parts = [re.sub(r"\(.*?\)", " ", name), *re.findall(r"\((.*?)\)", name)]
    out = []
    for part in parts:
        words = re.sub(r"[^a-z0-9 ]", " ", part.lower()).split()
        out.append(" ".join(words))
        while len(words) > 1 and words[-1] in _LEGAL_SUFFIXES:
            words.pop()
        out.append(" ".join(words))
    return [v for v in dict.fromkeys(out) if len(v) >= 3]


def mention_at(text: str, name: str, website: str) -> int:
    """Where the text first mentions the company, by domain name (e.g. 'fireflies') or name
    (see name_variants); -1 if it doesn't."""
    text = text.lower()
    domain = normalize_domain(website) or ""
    labels = domain.split(".")
    brand = labels[-2] if len(labels) >= 2 else ""
    if len(brand) >= 3 and (i := text.find(brand)) >= 0:
        return i
    # one space per replaced character, so positions still match the original text
    plain = re.sub(r"[^a-z0-9 ]", " ", text)
    found = [m.start() for v in name_variants(name) if (m := re.search(rf"\b{re.escape(v)}\b", plain))]
    return min(found) if found else -1


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
    return url, grounded or (html is not None and mention_at(html, found.name, found.website) >= 0)


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


def snowball(
    seed_name: str, seed_website: str, languages: list[str] = (), regions: list[str] = ()
) -> list[Found]:
    """(b) Find competitors of a known relevant company via comparison pages.

    With `languages` or `regions`, only competitors working in one of them: "alternatives to
    X" pages mostly list global, English-first tools, which add volume but not the language.
    """
    language_rule = f"""

The buyer needs data in: {", ".join(languages)}. Only return competitors with evidence of
working in at least one of these languages: a site written in it, the language named as
supported, or a base in a country where it is spoken. A generic "100+ languages" claim is
not evidence. Also look for local competitors, e.g. search "{seed_name} alternative" together
with a language or country name, and search in those languages.""" if languages else ""
    if regions:
        language_rule += f"""

The buyer needs data from: {", ".join(regions)}. Prefer competitors based in or serving these
regions, and search for local ones, e.g. "{seed_name} alternative" with a country name."""
    return _search(f"""Find companies that compete with {seed_name} ({seed_website}) —
businesses offering a similar product to a similar customer.

Search for pages such as:
- "alternatives to {seed_name}"
- "{seed_name} vs"
- "{seed_name} competitors"
- "best {seed_name} alternatives"

Collect the competing companies named on those pages. Do not return {seed_name} itself.{language_rule}""")


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


def _wanted(brief: Brief) -> str:
    return brief.summary("S")


def marketplace(brief: Brief, angle: str) -> list[Found]:
    """(d) Companies that already produce or sell this kind of data, searched from one angle."""
    return _search(f"""Find companies that already produce or sell data similar to this:
{_wanted(brief)}

{MARKETPLACE_ANGLES[angle].format(data_type=brief.description)}

Also search with the terms data vendors and researchers use for this kind of data, which
may differ from the wording above (e.g. "egocentric" for first-person video).""")


# (e) Holders that aren't companies, or that hold public bodies' recordings. What they publish
# often comes with a clear (sometimes open) licence, and the company-shaped channels above
# never find them.
PUBLIC_ANGLES = {
    "open corpora": """Search language-resource and research-data repositories (the ELRA/ELDA
catalogue, CLARIN centres, European Language Grid, LDC, Zenodo, university and national
language-technology sites) and research papers for corpora or datasets of this kind, e.g.
"{data_type} corpus", including ones built from parliament, court or broadcast recordings.
Return the organisation that publishes or distributes each one (repository, research
institute, university or public body). website must be that organisation's homepage, not a
general hosting site such as Hugging Face or GitHub. source_url must be the corpus page or
paper.""",
    "public recordings": """Search for public bodies that record and publish this kind of
data themselves (for meetings and speech: parliaments, regional and city councils, courts,
public broadcasters with session archives) and for the companies whose recording, streaming
or minutes platforms capture it for them. Return the public body or the platform company.
source_url must be the page with the recordings, or the page describing the platform.""",
}


def public_source(brief: Brief, angle: str) -> list[Found]:
    """(e) Organisations, not only companies, that publish this kind of data, from one angle."""
    local = (f"\n\nAlso search in {', '.join(brief.languages)}: local bodies, platforms and "
             "corpora are often only described in their own language.") if brief.languages else ""
    if brief.regions:
        local += f"\n\nLook especially for public bodies and repositories in {', '.join(brief.regions)}."
    return _search(f"""Find organisations — not only companies — that hold or publish data
similar to this:
{_wanted(brief)}

{PUBLIC_ANGLES[angle].format(data_type=brief.description)}{local}""")


def _kinds(hypotheses: list[HolderHypothesis]) -> str:
    return "\n".join(f"- {h.category}" for h in hypotheses[:5])


def distressed(brief: Brief, hypotheses: list[HolderHypothesis]) -> list[Found]:
    """(f) Companies that held this kind of data and are insolvent, shut down, retired the
    product after an acquisition, or are for sale: their data is often sold with the assets or
    sits forgotten in storage."""
    return _search(f"""Find companies that held data similar to this and are insolvent, in
administration or liquidation, being wound up, have shut down, retired the product after being
acquired, or are up for sale:
{_wanted(brief)}

Kinds of business likely to hold it:
{_kinds(hypotheses)}

Search insolvency notices and gazettes, administrators' and liquidators' asset sale pages,
bankruptcy filings that list data as an asset, liquidation auctions, marketplaces where small
companies are sold (e.g. Acquire.com, Flippa), "we're shutting down" and "sunsetting" posts,
startup obituary lists, and "acquired" news where the product was then switched off.
source_url must be the notice, sale page, listing or article.""")


def signals(brief: Brief, hypotheses: list[HolderHypothesis]) -> list[Found]:
    """(g) Untapped holders found by what their own pages give away: privacy policies that say
    they record and keep the data, app data-safety labels, job ads for in-house transcribers or
    annotators, and scale claims."""
    return _search(f"""Find companies that hold data similar to this without selling it, by
looking for signs on their own pages:
{_wanted(brief)}

Why companies end up holding data like this:
{archetypes.guide(brief.chosen("modality"))}

Search for:
- privacy policies and terms that say they record and keep this kind of data, e.g. "calls may
  be recorded", "we retain recordings for", "used to improve our services and train our models"
- app store data-safety sections that list collecting this kind of data
- job ads for in-house transcriptionists, call-quality analysts or data annotators
- annual reports and case studies with scale claims, e.g. "40 million calls handled"
Skip companies that already sell or license data. source_url must be the policy, listing, job
ad or report.""")


def suppliers(brief: Brief, hypotheses: list[HolderHypothesis]) -> list[Found]:
    """(h) Untapped holders found through the customer lists of the tools they run on, and
    member and exhibitor lists of their industry."""
    tools = ", ".join(archetypes.supplier_tools(brief.chosen("modality")))
    return _search(f"""Find companies likely to hold data similar to this, through the
suppliers and associations they use:
{_wanted(brief)}

Kinds of business likely to hold it:
{_kinds(hypotheses)}

Search:
- case studies, customer stories and logo walls of tools these companies run on ({tools}).
  Every customer that records this kind of data with the tool is a candidate.
- member directories of their trade associations, and exhibitor and sponsor lists of their
  industry events.
Return the customer or member companies, not the tool vendor or the association. source_url
must be the case study, customer page or directory.""")


def records(brief: Brief, hypotheses: list[HolderHypothesis]) -> list[Found]:
    """(i) Untapped holders found in public records: tenders, finished research projects,
    data-protection registers and papers that thank a company for data."""
    return _search(f"""Find organisations likely to hold data similar to this, through public
records:
{_wanted(brief)}

Kinds of business likely to hold it:
{_kinds(hypotheses)}

Search:
- public tenders and contract awards for services that capture this kind of data (e.g. call
  handling, transcription, CCTV, fleet cameras) on TED, Contracts Finder, SAM.gov, Etimad and
  national portals: return the supplier that won
- research project databases (CORDIS, UKRI, NIH RePORTER) for finished projects that collected
  this kind of data: return the partner organisations
- data-protection registers and published impact assessments that mention recording it
- research papers and Kaggle competitions where a company provided this kind of data
source_url must be the tender, project, register entry or paper.""")
