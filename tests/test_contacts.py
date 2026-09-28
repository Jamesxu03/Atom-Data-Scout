from worker.crawl import CrawlResult, Page
from worker.schemas import Candidate, Contact
from worker.steps.contacts import (
    FoundContact,
    FoundContacts,
    from_search,
    from_website,
    merge_contacts,
    verify,
)

PRIVACY_HTML = """
<p>Contact our DPO at <a href="mailto:DPO@acme.com">DPO@acme.com</a> or privacy@acme.com.</p>
<img src="logo@2x.png"> <script>dsn='abc@sentry.io'</script>
<a href="tel:+44 20 7946 0001">Call us</a> hello@acme.com hello@eu.acme.com
<p>e.g. yourname@youremployer.com, forms by support@formvendor.co</p>
"""


def _crawl():
    return CrawlResult(website="https://acme.com",
                       homepage=Page(url="https://acme.com", text="x"),
                       privacy=Page(url="https://acme.com/privacy", text="x"))


def test_from_website_extracts_emails_and_tel_links():
    pages = {"https://acme.com": "<p>no contacts here</p>", "https://acme.com/privacy": PRIVACY_HTML}
    got = from_website(_crawl(), html_for=pages.__getitem__)

    emails = {c.email: c for c in got if c.email}
    # deduped; junk, placeholders and other companies' addresses dropped; subdomains kept
    assert set(emails) == {"dpo@acme.com", "privacy@acme.com", "hello@acme.com", "hello@eu.acme.com"}
    assert emails["dpo@acme.com"].role == "Privacy / DPO inbox"
    assert emails["hello@acme.com"].role == "General inbox"
    assert [c.phone for c in got if c.phone] == ["+44 20 7946 0001"]
    assert all(c.verified and c.origin == "website" and c.source_url.endswith("/privacy") for c in got)


def test_verify():
    page = "<p>Jane Doe, CEO — jane@acme.com — +44 (0)20 7946 0002</p>"
    fetch = lambda url: page  # noqa: E731
    assert verify(Contact(email="JANE@acme.com", source_url="u"), fetch)
    assert not verify(Contact(email="john@acme.com", source_url="u"), fetch)  # not on the page
    assert verify(Contact(phone="+44 20 7946 0002", source_url="u"), fetch)  # formatting differs
    assert verify(Contact(name="Jane Doe", source_url="u"), fetch)
    assert not verify(Contact(name="Jane Doe", source_url="u"), lambda u: (_ for _ in ()).throw(OSError()))


def test_from_search_cleans_and_verifies(fake_ask):
    fake_ask.responses[FoundContacts] = FoundContacts(contacts=[
        FoundContact(name="Jane Doe", role="CEO", email="jane@acme.com", phone=None, linkedin_url=None, source_url="https://acme.com/team"),
        FoundContact(name="Bob", role="CTO", email="not an email", phone=None, linkedin_url=None, source_url="https://x.com/b"),
        FoundContact(name=None, role=None, email=None, phone=None, linkedin_url="https://linkedin.com/in/x", source_url="s"),
    ])
    cand = Candidate(name="Acme", website="https://acme.com", domain="acme.com", channels=["product_search"], source_urls=[])
    got = from_search(cand, verifier=lambda c: c.email == "jane@acme.com")

    assert [(c.name, c.email, c.verified) for c in got] == [("Jane Doe", "jane@acme.com", True), ("Bob", None, False)]
    (call,) = fake_ask.calls
    assert call.use_search
    assert "Never guess or construct an email" in call.prompt


def test_merge_prefers_named_and_verified():
    got = merge_contacts([
        Contact(email="jane@acme.com", source_url="site", origin="website", verified=True),
        Contact(name="Jane Doe", role="CEO", email="Jane@acme.com", source_url="search", verified=False),
        Contact(name="Unverified Person", source_url="s", verified=False),
        Contact(email="info@acme.com", source_url="site", origin="website", verified=True),
    ])
    assert len(got) == 3
    jane = got[0]
    assert (jane.name, jane.verified) == ("Jane Doe", True)  # kept name, inherited verification
    assert got[-1].name == "Unverified Person"
