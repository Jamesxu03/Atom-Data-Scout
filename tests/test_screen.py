from tests.test_hypotheses import SPEC
from worker.config import SCREEN_BATCH_SIZE
from worker.schemas import Candidate
from worker.steps.screen import ScreenBatch, ScreenItem, relevant, screen


def _cand(domain):
    return Candidate(name=domain, website=f"https://{domain}", domain=domain,
                     channels=["product_search"], source_urls=[])


def _label_by_prefix(prompt):
    """yes-* -> yes, maybe-* -> maybe, else no; skips 'silent-*' to test missing labels."""
    items = []
    for line in prompt.splitlines():
        if line.startswith("### "):
            domain = line[4:].split(" ")[0]
            if domain.startswith("silent"):
                continue
            label = domain.split("-")[0] if domain.split("-")[0] in ("yes", "maybe") else "no"
            holder = "technology" if "tech" in domain else "unrelated" if label == "no" else "by_product"
            items.append(ScreenItem(domain=domain.upper(), holder_type=holder, data_owner="unclear",
                                    label=label, reason=f"because {domain}"))
    return ScreenBatch(results=items)


def test_screen_labels_and_filters(fake_ask):
    fake_ask.responses[ScreenBatch] = _label_by_prefix
    cands = [_cand(d) for d in ["yes-a.com", "maybe-b.com", "no-c.com", "down.com", "silent-d.com"]]

    def fetch(url):
        if "down.com" in url:
            raise ConnectionError("refused")
        return "homepage text " * 500

    screen(SPEC, cands, fetch=fetch, progress=lambda _: None)
    by = {c.domain: c for c in cands}

    assert by["yes-a.com"].relevance == "yes"
    assert by["yes-a.com"].relevance_reason == "because yes-a.com"
    assert (by["yes-a.com"].holder_type, by["yes-a.com"].data_owner) == ("by_product", "unclear")
    assert by["no-c.com"].relevance == "no"
    assert by["down.com"].relevance == "maybe"  # unreachable is kept, not judged
    assert by["down.com"].holder_type is None
    assert "unreachable" in by["down.com"].relevance_reason
    assert by["silent-d.com"].relevance == "maybe"  # model skipped it
    assert [c.domain for c in relevant(cands)][0] == "yes-a.com"
    assert "no-c.com" not in [c.domain for c in relevant(cands)]

    (call,) = fake_ask.calls  # one batch; unreachable homepage never sent to the model
    assert "down.com" not in call.prompt
    assert call.prompt.count("homepage text") < 2000  # text is truncated per company


def test_screen_batches(fake_ask):
    fake_ask.responses[ScreenBatch] = _label_by_prefix
    cands = [_cand(f"yes-{i}.com") for i in range(SCREEN_BATCH_SIZE * 2 + 1)]
    screen(SPEC, cands, fetch=lambda _: "text", progress=lambda _: None)
    assert len(fake_ask.calls) == 3
    assert all(c.relevance == "yes" for c in cands)


def _connect_error(cause: Exception):
    """httpx.ConnectError the way httpx raises it: chained from the socket/ssl error."""
    import httpx

    try:
        try:
            raise cause
        except Exception as inner:
            raise httpx.ConnectError(str(inner)) from inner
    except httpx.ConnectError as e:
        return e


def test_only_dns_failure_counts_as_dead_site(fake_ask):
    import socket
    import ssl

    import httpx

    fake_ask.responses[ScreenBatch] = _label_by_prefix
    names = ["made-up.com", "refused.com", "bad-cert.com", "blocked.com", "slow.com"]
    cands = [_cand(d) for d in names]
    req = httpx.Request("GET", "https://blocked.com")
    errors = {
        "made-up": _connect_error(socket.gaierror(8, "nodename nor servname provided")),
        "refused": _connect_error(ConnectionRefusedError(61, "Connection refused")),
        "bad-cert": _connect_error(ssl.SSLCertVerificationError("certificate has expired")),
        "blocked": httpx.HTTPStatusError("403", request=req, response=httpx.Response(403, request=req)),
        "slow": httpx.ReadTimeout("timed out"),
    }

    def fetch(url):
        raise next(e for key, e in errors.items() if key in url)

    screen(SPEC, cands, fetch=fetch, progress=lambda _: None)
    by = {c.domain: c for c in cands}
    assert (by["made-up.com"].relevance, by["made-up.com"].relevance_reason) == ("no", "dead website: domain doesn't exist")
    for d in names[1:]:  # real companies do these to crawlers: keep them
        assert by[d].relevance == "maybe", d
    assert fake_ask.calls == []  # nothing reachable, nothing sent to the model


def test_only_unlabelled_candidates_are_screened(fake_ask):
    fake_ask.responses[ScreenBatch] = _label_by_prefix
    done = _cand("yes-old.com")
    done.relevance, done.relevance_reason = "no", "screened earlier"
    new = _cand("yes-new.com")
    fetched = []
    screen(SPEC, [done, new], fetch=lambda url: fetched.append(url) or "text", progress=lambda _: None)
    assert fetched == ["https://yes-new.com"]
    assert (done.relevance, new.relevance) == ("no", "yes")


def test_relevant_ranks_by_corroboration_not_arrival_order():
    lone = _cand("lone.com")  # found once, by one channel
    strong = _cand("strong.com")
    strong.channels = ["product_search", "directory", "snowball"]
    strong.hypotheses = ["h1", "h2"]
    maybe = _cand("maybe.com")
    maybe.channels = ["product_search", "directory", "snowball", "marketplace"]
    for c, label in ((lone, "yes"), (strong, "yes"), (maybe, "maybe")):
        c.relevance = label
    assert [c.domain for c in relevant([lone, maybe, strong])] == ["strong.com", "lone.com", "maybe.com"]


def test_technology_company_is_never_yes(fake_ask):
    fake_ask.responses[ScreenBatch] = _label_by_prefix
    tech = _cand("yes-tech.com")  # model says yes, but it sells tracking software, not footage
    screen(SPEC, [tech], fetch=lambda _: "text", progress=lambda _: None)
    assert (tech.relevance, tech.holder_type) == ("maybe", "technology")


def test_rank_by_holder_type_before_corroboration():
    def screened(domain, relevance, holder, channels=("product_search",)):
        c = _cand(domain)
        c.relevance, c.holder_type, c.channels = relevance, holder, list(channels)
        return c

    famous = screened("famous.com", "yes", "by_product", ["product_search", "directory", "snowball"])
    vendor = screened("vendor.com", "yes", "vendor", ["marketplace"])
    niche = screened("niche.com", "yes", "by_product")
    tech = screened("tech.com", "maybe", "technology", ["product_search", "directory"])
    maybe_vendor = screened("maybe-vendor.com", "maybe", "vendor")
    order = [c.domain for c in relevant([tech, niche, famous, maybe_vendor, vendor])]
    assert order == ["vendor.com", "famous.com", "niche.com", "maybe-vendor.com", "tech.com"]
