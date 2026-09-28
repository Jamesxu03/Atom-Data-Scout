from worker.config import SCREEN_BATCH_SIZE
from worker.schemas import Candidate
from worker.steps.screen import ScreenBatch, ScreenItem, relevant, screen
from tests.test_hypotheses import SPEC


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
            items.append(ScreenItem(domain=domain.upper(), label=label, reason=f"because {domain}"))
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
    assert by["no-c.com"].relevance == "no"
    assert by["down.com"].relevance == "maybe"  # unreachable is kept, not judged
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
