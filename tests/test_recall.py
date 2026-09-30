from eval.discovery_recall import Seed, load_seeds, match, report
from worker.discovery import Discovery, Hit, merge


def _discovery():
    hits = [
        Hit(channel="product_search", name="Otter.ai", website="https://otter.ai", source_url="s"),
        Hit(channel="directory", name="Otter", website="https://www.otter.ai", source_url="s"),
        Hit(channel="snowball", name="Fireflies", website="https://fireflies.ai", source_url="s", seed="Otter.ai"),
        Hit(channel="marketplace", name="Acme Data Ltd", website="https://acmedata.io", source_url="s"),
    ]
    cands = merge(hits)
    fits = {"otter.ai": 4.0, "fireflies.ai": 4.5, "acmedata.io": 1.2}
    for c in cands:
        c.reason, c.fit, c.excluded = "r", fits[c.domain], c.domain == "acmedata.io"
    return Discovery(hits=hits, candidates=cands)


SEEDS = [
    Seed(name="Otter", website="otter.ai"),
    Seed(name="Fireflies.ai", website="https://www.fireflies.ai/"),
    Seed(name="ACME Data Limited"),  # no website: match by name
    Seed(name="Missing Co", website="missing.com"),
]


def test_match_by_domain_then_name():
    found = {m.seed.name: m.candidate.domain if m.candidate else None for m in match(SEEDS, _discovery().candidates)}
    assert found == {"Otter": "otter.ai", "Fireflies.ai": "fireflies.ai",
                     "ACME Data Limited": "acmedata.io", "Missing Co": None}


def test_report():
    r = " ".join(report(SEEDS, _discovery()).split())  # ignore column padding
    assert "3/4 (75%)" in r
    assert "product_search 1/4 (25%) only this channel: 0" in r
    assert "snowball 1/4 (25%) only this channel: 1" in r
    assert "marketplace 1/4 (25%) only this channel: 1" in r
    assert "- Missing Co missing.com" in r
    assert "excluded 1" in r and "relevant 2" in r  # screen wrongly dropped Acme
    assert "Rank of found seeds among 3: median 2, top 10: 3" in r
    assert "#1 Fireflies.ai 4.5" in r and "#2 Otter 4.0" in r
    assert "WARNING" in r and "Otter" in r  # Otter was a snowball seed


def test_empty_seed_file_is_friendly(tmp_path):
    p = tmp_path / "seeds.csv"
    p.write_text("name,website,notes\n")
    assert load_seeds(p) == []
    assert "No seeds" in report([], _discovery())
