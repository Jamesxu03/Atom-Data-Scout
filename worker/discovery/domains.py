"""Domain normalisation for dedupe, and sites that list companies rather than being one."""

from urllib.parse import urlparse

# Directories, marketplaces, social and news sites: sources, never candidates
AGGREGATOR_DOMAINS = {
    "g2.com", "capterra.com", "capterra.co.uk", "producthunt.com", "getapp.com",
    "softwareadvice.com", "alternativeto.net", "saasworthy.com", "trustradius.com",
    "sourceforge.net", "crunchbase.com", "linkedin.com", "twitter.com", "x.com",
    "facebook.com", "youtube.com", "medium.com", "reddit.com", "wikipedia.org",
    "github.com", "techcrunch.com", "forbes.com",
    "pitchbook.com", "craft.co", "growjo.com",
    "datarade.ai", "aws.amazon.com", "snowflake.com", "huggingface.co", "kaggle.com",
    "vertexaisearch.cloud.google.com",
}


def normalize_domain(url: str) -> str | None:
    """'https://www.Otter.ai/pricing' -> 'otter.ai'. None if no usable host."""
    url = (url or "").strip()
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host if "." in host else None


def is_aggregator(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in AGGREGATOR_DOMAINS)


def is_excluded(domain: str, exclusions: list[str]) -> bool:
    """domain is one of the brief's exclusions (given as domains or URLs) or a subdomain of one."""
    for ex in exclusions:
        ex = normalize_domain(ex)
        if ex and (domain == ex or domain.endswith("." + ex)):
            return True
    return False
