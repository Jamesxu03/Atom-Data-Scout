"""Discovery channels. Each is one Gemini call with Google Search returning companies + source URLs.

Every channel takes a Context: the local market to search natively in (WS2), and in later
rounds the companies already found, the queries already tried and good holders to find more
like (WS3).
"""

import re
from dataclasses import dataclass

import httpx

from worker import archetypes, llm
from worker.brief_template import Brief
from worker.config import DISCOVERY, TRIGGER_YEARS
from worker.crawl import fetch_html_quick
from worker.discovery.domains import normalize_domain
from worker.schemas import Found, FoundList, HolderHypothesis, LocalHooks, Market
from worker.verify import NO_SPACE_SCRIPT

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
# Legal forms written without spaces (株式会社 Acme, 某某有限公司), or as a separate leading word (شركة)
_LEGAL_AFFIXES = ("株式会社", "有限会社", "合同会社", "股份有限公司", "有限公司", "公司", "شركة")
_NOT_WORD = re.compile(r"[^\w ]|_")  # any letter or digit, in any script, is kept


def _plain(text: str) -> str:
    """Lowercased, punctuation replaced by spaces one character for one, so positions still
    match the original text."""
    return _NOT_WORD.sub(" ", text.lower())


def name_variants(name: str) -> list[str]:
    """The ways a page may write a company's name, lowercased: the name without any bracketed
    part, each bracketed part ("Homepoint (Home Point Capital)"), and each without a legal
    suffix ("Ebby Halliday Companies" -> "ebby halliday") or legal form ("株式会社アクメ" -> "アクメ").
    Names in any script are kept."""
    parts = [re.sub(r"\(.*?\)", " ", name), *re.findall(r"\((.*?)\)", name)]
    out = []
    for part in parts:
        words = _plain(part).split()
        out.append(" ".join(words))
        while len(words) > 1 and words[-1] in _LEGAL_SUFFIXES:
            words.pop()
        bare = " ".join(words)
        for affix in _LEGAL_AFFIXES:
            bare = bare.removeprefix(affix).removesuffix(affix).strip()
        out.append(bare)
    # three characters for alphabets; two for Chinese or Japanese, where a name is often that short
    return [v for v in dict.fromkeys(out) if len(v) >= (2 if NO_SPACE_SCRIPT.search(v) else 3)]


def mention_at(text: str, name: str, website: str) -> int:
    """Where the text first mentions the company, by domain name (e.g. 'fireflies') or name
    (see name_variants); -1 if it doesn't."""
    text = text.lower()
    domain = normalize_domain(website) or ""
    labels = domain.split(".")
    brand = labels[-2] if len(labels) >= 2 else ""
    if len(brand) >= 3 and (i := text.find(brand)) >= 0:
        return i
    plain = _plain(text)
    found = []
    for v in name_variants(name):
        if NO_SPACE_SCRIPT.search(v):  # no spaces, so no word boundaries to look for
            i = plain.find(v)
        else:
            i = m.start() if (m := re.search(rf"\b{re.escape(v)}\b", plain)) else -1
        if i >= 0:
            found.append(i)
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


@dataclass(frozen=True)
class Context:
    """What one call is told besides its own question."""

    market: Market | None = None  # search this country natively, in its language
    hooks: LocalHooks | None = None  # the market's domain filter, tender portal, laws and tools
    excluded: tuple[str, ...] = ()  # "Name (domain)" already found in this run
    tried: tuple[str, ...] = ()  # queries already tried for this channel
    examples: tuple[str, ...] = ()  # good holders found so far: find more like these


NO_CONTEXT = Context()


def _local(ctx: Context) -> str:
    m, hooks = ctx.market, ctx.hooks
    if not m:
        return ""
    site = (hooks and hooks.site_filter) or (f"site:{m.tld}" if m.tld else "")
    lines = [(f"Search {m.country} only, in {m.language}: write every query in {m.language} the way local "
              "organisations write about themselves, not as word-for-word translations of English queries.")]
    if site:
        lines.append(f"Prefer local sites ({site}).")
    if hooks and hooks.associations:
        lines.append(f"Local trade associations to look through: {', '.join(hooks.associations)}.")
    return "\n\n" + " ".join(lines)


def _exclusions(ctx: Context) -> str:
    out = ""
    if ctx.excluded:
        out += ("\n\nAlready found, do not return any of these: " + ", ".join(ctx.excluded) + ".")
    if ctx.tried:
        out += ("\n\nQueries already tried here: " + "; ".join(ctx.tried) + ". Run different ones: other "
                "sub-sectors, company sizes, cities"
                + (f" and {ctx.market.language} wording" if ctx.market else " and local languages") + ".")
    return out


def _search(prompt: str, ctx: Context = NO_CONTEXT, queries: list[str] | None = None) -> list[Found]:
    n = DISCOVERY["companies_per_call"]
    result = llm.ask(prompt + _local(ctx) + _exclusions(ctx) + "\n" + RULES.format(n=n), FoundList,
                     use_search=True, queries=queries, **llm.step("discovery"))
    return result.companies[:n]


def product_search(h: HolderHypothesis, ctx: Context = NO_CONTEXT, queries: list[str] | None = None) -> list[Found]:
    """(a) Search in the words the organisations use about themselves."""
    lines = "\n".join(f"- {q}" for q in h.search_queries)
    like = ("\n\nGood finds so far; find more organisations like these:\n" + "\n".join(f"- {e}" for e in ctx.examples)
            if ctx.examples else "")
    return _search(f"""Find real organisations of this kind: {h.category}.
Why they matter: {h.rationale}

Search with these queries (and close variations):
{lines}

Include small, niche, regional and defunct organisations, not only market leaders.{like}""", ctx, queries)


def snowball(
    seed_name: str, seed_website: str, languages: list[str] = (), regions: list[str] = (),
    ctx: Context = NO_CONTEXT, queries: list[str] | None = None,
) -> list[Found]:
    """(b) Organisations similar to a good holder: its peers in the same industry and country,
    which hold the same kind of data for the same reason. Not "alternatives to X" pages: those
    are vendor listicles, and the peers of an airline are other airlines, not software.

    With `languages` or `regions`, only peers working in one of them.
    """
    language_rule = f"""

The buyer needs data in: {", ".join(languages)}. Only return organisations with evidence of
working in at least one of these languages: a site written in it, the language named as
supported, or a base in a country where it is spoken. A generic "100+ languages" claim is
not evidence. Search in those languages too, e.g. "{seed_name} alternative", "Alternative zu
{seed_name}", "{seed_name} 替代" written in the language, or "companies like {seed_name}" with
a country name.""" if languages else ""
    if regions:
        language_rule += f"""

The buyer needs data from: {", ".join(regions)}. Prefer organisations based in or serving these
regions, and search for local ones, e.g. "companies like {seed_name}" with a country name."""
    return _search(f"""Find organisations similar to {seed_name} ({seed_website}) in the same
industry and country: its peers, which do the same kind of work and so hold the same kind of
data for the same reason.

Search for:
- "companies like {seed_name}" and "{seed_name} competitors" with its industry and country
- member lists of its trade association, and industry rankings or awards in its country
- organisations named alongside it in tenders, news and case studies

Return organisations that do the same work, not software vendors serving them. Do not return
{seed_name} itself.{language_rule}""", ctx, queries)


def directory(h: HolderHypothesis, ctx: Context = NO_CONTEXT, queries: list[str] | None = None) -> list[Found]:
    """(c) Software directory / category pages (G2, Capterra, Product Hunt...). They list
    vendors, whose customers are then searched by the suppliers channel."""
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
where each vendor is listed.""", ctx, queries)


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


def marketplace(brief: Brief, angle: str, ctx: Context = NO_CONTEXT, queries: list[str] | None = None) -> list[Found]:
    """(d) Companies that already produce or sell this kind of data, searched from one angle."""
    return _search(f"""Find companies that already produce or sell data similar to this:
{_wanted(brief)}

{MARKETPLACE_ANGLES[angle].format(data_type=brief.description)}

Also search with the terms data vendors and researchers use for this kind of data, which
may differ from the wording above (e.g. "egocentric" for first-person video).""", ctx, queries)


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


def public_source(brief: Brief, angle: str, ctx: Context = NO_CONTEXT,
                  queries: list[str] | None = None) -> list[Found]:
    """(e) Organisations, not only companies, that publish this kind of data, from one angle."""
    local = (f"\n\nAlso search in {', '.join(brief.languages)}: local bodies, platforms and "
             "corpora are often only described in their own language.") if brief.languages else ""
    if brief.regions:
        local += f"\n\nLook especially for public bodies and repositories in {', '.join(brief.regions)}."
    return _search(f"""Find organisations — not only companies — that hold or publish data
similar to this:
{_wanted(brief)}

{PUBLIC_ANGLES[angle].format(data_type=brief.description)}{local}""", ctx, queries)


def _kinds(hypotheses: list[HolderHypothesis], focus: HolderHypothesis | None = None) -> str:
    """The kinds of organisation to search for: the focus alone, or the first five holders
    (vendors last, since their customers are the holders)."""
    if focus:
        return f"- {focus.category}: {focus.rationale}"
    picked = [h for h in hypotheses if h.side != "vendor"][:5] or hypotheses[:5]
    return "\n".join(f"- {h.category}" for h in picked)


# (f) Companies whose data may come up for sale: one call per angle
DISTRESSED_ANGLES = {
    "distress": """Find companies that held data similar to this and are insolvent, in
administration or liquidation, being wound up, have shut down, retired the product after being
acquired, or are up for sale:
{wanted}

Kinds of business likely to hold it:
{kinds}

Search insolvency notices and gazettes, administrators' and liquidators' asset sale pages,
bankruptcy filings that list data as an asset, liquidation auctions, marketplaces where small
companies are sold (e.g. Acquire.com, Flippa), "we're shutting down" and "sunsetting" posts,
startup obituary lists, and "acquired" news where the product was then switched off.
source_url must be the notice, sale page, listing or article.""",
    "precedent": """Find organisations that have licensed, sold or shared data similar to this
before, released it as a dataset, or announced a data partnership with an AI company:
{wanted}

Kinds of organisation likely to hold it:
{kinds}

Search press releases and news about data licensing deals and AI data partnerships, AI
companies' lists of data partners, research papers and competitions that thank an organisation
for providing data, and organisations' own "data partnerships" or "AI training data" pages.
Return the organisation that held the data, not the AI company. source_url must be the
announcement, article, paper or page.""",
    "regulated": """Find big brands and regulated institutions (banks, insurers, airlines,
telcos, hospital groups, utilities) that hold data similar to this, where something changed in
the last {years} years that could let them sell or share it: they closed or sold a contact
centre, unit or brand, entered administration or run-off, merged and retired systems, or a new
law or regulator decision lets them reuse or share it:
{wanted}

Search for those events, e.g.:
{triggers}
Return the institution, not the news site. source_url must be the article, notice or
announcement.""",
}


def distressed(brief: Brief, hypotheses: list[HolderHypothesis], angle: str = "distress",
               ctx: Context = NO_CONTEXT, queries: list[str] | None = None) -> list[Found]:
    """(f) Organisations whose data may come up for sale, from one angle: distress (insolvent,
    shut down, for sale), precedent (it has licensed or shared such data before), or regulated
    (a big institution where something changed: WS6)."""
    regulated = [q for h in hypotheses if h.side == "regulated" for q in h.search_queries]
    triggers = "\n".join(f"- {q}" for q in regulated[:8]) or (
        '- "<sector> enters administration"\n- "<sector> closes call centre"\n'
        '- "<sector> data sharing law"')
    return _search(DISTRESSED_ANGLES[angle].format(
        wanted=_wanted(brief), kinds=_kinds(hypotheses), years=TRIGGER_YEARS, triggers=triggers), ctx, queries)


def signals(brief: Brief, hypotheses: list[HolderHypothesis], focus: HolderHypothesis | None = None,
            ctx: Context = NO_CONTEXT, queries: list[str] | None = None) -> list[Found]:
    """(g) Untapped holders found by what their own pages give away: privacy policies that say
    they record and keep the data, app data-safety labels, job ads for in-house transcribers or
    call-quality analysts, and scale claims. Organisations, not the vendors of their tools."""
    return _search(f"""Find organisations that hold data similar to this without selling it, by
looking for signs on their own pages:
{_wanted(brief)}

Kinds of organisation to look for:
{_kinds(hypotheses, focus)}

Why organisations end up holding data like this:
{archetypes.guide(brief.chosen("modality"))}

Search for:
- privacy policies and terms that say they record and keep this kind of data, together with
  an industry, e.g. "calls may be recorded for training purposes" insurance,
  "we retain recordings for" university, "used to improve our services and train our models"
- app store data-safety sections that list collecting this kind of data
- job ads for in-house transcriptionists, call-quality analysts or data annotators at
  organisations that are not software vendors
- annual reports and case studies with scale claims, e.g. "40 million calls handled"
Return the organisation that records and keeps the data, never the software vendor whose tool
records it. Skip companies that already sell or license data. source_url must be the policy,
listing, job ad or report.""", ctx, queries)


def _brand(name_or_url: str) -> str:
    domain = normalize_domain(name_or_url) if "." in name_or_url else None
    if domain:
        labels = domain.split(".")
        return labels[-2] if len(labels) >= 2 else domain
    return re.sub(r"[^a-z0-9]", "", name_or_url.lower())


def is_tool(f: Found, tools: list[str], tool_domains: list[str] = ()) -> bool:
    """The tool itself came back instead of a customer: same domain, or same brand."""
    domain = normalize_domain(f.website) or ""
    if domain and any(domain == d or domain.endswith("." + d) for d in tool_domains):
        return True
    brands = {_brand(t) for t in tools} | {_brand(d) for d in tool_domains}
    return _brand(f.website) in brands or re.sub(r"[^a-z0-9]", "", f.name.lower()) in brands


def suppliers(brief: Brief, tools: list[str], hypotheses: list[HolderHypothesis], ctx: Context = NO_CONTEXT,
              queries: list[str] | None = None, tool_domains: list[str] = ()) -> list[Found]:
    """(h) Organisations named as customers of these tools (usually one), from the tools' own
    customer stories, case studies and logo walls. The tools themselves are dropped here, since
    the model returns them despite being told not to."""
    names = " and ".join(tools)
    q = tools[0]
    found = _search(f"""Find organisations named as customers of {names} that record data
similar to this with it:
{_wanted(brief)}

Kinds of organisation likely to use it:
{_kinds(hypotheses)}

Search "{q} customer story", "{q} case study" with an industry, "{q} customers", and the
customer stories, case studies and logo walls on {names}'s own site. Every customer that
records this kind of data with it is a candidate.
Return the customer organisations, never {names} itself or any other software vendor.
source_url must be the case study, customer story or customer page.""", ctx, queries)
    return [f for f in found if not is_tool(f, tools, tool_domains)]


def records(brief: Brief, hypotheses: list[HolderHypothesis], focus: HolderHypothesis | None = None,
            ctx: Context = NO_CONTEXT, queries: list[str] | None = None) -> list[Found]:
    """(i) Untapped holders found in public records: tenders (the buyer whose data is recorded
    as well as the supplier that won), finished research projects, data-protection registers
    and papers that thank a company for data."""
    portal = (f"\n- {ctx.hooks.tender_portal}, the national tender portal: search it in "
              f"{ctx.market.language}") if ctx.hooks and ctx.hooks.tender_portal and ctx.market else ""
    law = (f"\n- registers and filings under {ctx.hooks.retention_law}"
           if ctx.hooks and ctx.hooks.retention_law else "")
    return _search(f"""Find organisations likely to hold data similar to this, through public
records:
{_wanted(brief)}

Kinds of organisation likely to hold it:
{_kinds(hypotheses, focus)}

Search:
- public tenders and contract awards for services that capture this kind of data (e.g. call
  handling, transcription, CCTV, fleet cameras) on TED, Contracts Finder, SAM.gov, Etimad and
  national portals: return the buyer (the organisation whose calls or data are recorded) and
  the supplier that won{portal}
- research project databases (CORDIS, UKRI, NIH RePORTER) for finished projects that collected
  this kind of data: return the partner organisations
- data-protection registers and published impact assessments that mention recording it{law}
- research papers and Kaggle competitions where a company provided this kind of data
source_url must be the tender, project, register entry or paper.""", ctx, queries)
