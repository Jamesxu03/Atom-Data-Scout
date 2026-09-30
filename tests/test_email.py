from tests.test_hypotheses import BRIEF, brief_with
from worker.brief_template import UNCLEAR, Pick
from worker.schemas import Candidate, Check, Contact, EmailDraft, Evidence, Research
from worker.steps.email import build_prompt, draft_email


def _cand(holder_type="by_product"):
    return Candidate(
        name="Acme", website="https://acme.com", domain="acme.com", channels=["product_search"], source_urls=[],
        reason="AI notetaker for sales teams", holder_type=holder_type,
        research=Research(
            data_held="recorded sales meetings with transcripts", licence=UNCLEAR,
            licence_evidence=[Evidence(quote="Customers own their recordings", url="https://acme.com/terms")],
            volume_estimate="about 2 million meetings",
            volume_score=5,
            volume_evidence=[Evidence(quote="2 million meetings recorded", url="https://acme.com")],
            checks=[Check(category="personal_data", verdict="meets", finding="recordings are pseudonymised",
                          evidence=[Evidence(quote="q", url="u")])],
        ),
    )


JANE = Contact(name="Jane Doe", role="CEO", email="jane@acme.com", source_url="s", verified=True)


def test_prompt_built_from_brief_and_evidence():
    brief = brief_with(permitted_use=Pick(values=["Training for a commercial product"]),
                       sample=Pick(values=["Required"]), budget=Pick(values=["Under £10k"]))
    p = build_prompt(brief, _cand(), JANE)
    assert "Jane Doe (CEO) at Acme" in p
    assert "Multi-speaker meeting audio" in p  # the brief's summary
    assert '"Customers own their recordings" (https://acme.com/terms)' in p
    assert "about 2 million meetings" in p
    assert "with their customers' consent" in p  # the by-product ask
    assert "Personal data: recordings are pseudonymised" in p
    assert "- Permitted use needed: Training for a commercial product" in p and "- Sample before purchase: Required" in p
    assert "£10k" not in p  # the budget is never passed on
    assert "British English" in p and "Never state or suggest a price" in p


def test_ask_depends_on_holder_type():
    assert "price" in build_prompt(BRIEF, _cand("vendor"), JANE).split("Ask:")[1].split("\n")[0]
    assert "licence terms" in build_prompt(BRIEF, _cand("public"), None)
    assert "the team at Acme" in build_prompt(BRIEF, _cand("public"), None)


def test_draft_is_addressed_to_the_contact(fake_ask):
    fake_ask.responses[EmailDraft] = EmailDraft(to="made-up@x.com", subject="Meeting recordings", body="Hello")
    draft = draft_email(BRIEF, _cand(), JANE)
    assert (draft.to, draft.subject) == ("jane@acme.com", "Meeting recordings")
    assert not fake_ask.calls[0].use_search
