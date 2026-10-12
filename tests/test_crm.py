"""Clients, the search board and outreach, in a temp database (tests/conftest.py)."""

from datetime import timedelta

import pytest

from worker import crm


def test_clients_get_unique_ids_and_sort_by_name():
    otter = crm.create_client("Otter AI", note="  buys meeting audio ")
    again = crm.create_client("Otter AI")
    crm.create_client("acme")
    assert (otter.id, again.id) == ("otter-ai", "otter-ai-2")
    assert otter.note == "buys meeting audio"
    assert [c.name for c in crm.clients()] == ["acme", "Otter AI", "Otter AI"]
    crm.set_note("otter-ai", "wants 1,000 hours")
    assert crm.get_client("otter-ai").note == "wants 1,000 hours"
    assert crm.get_client("nobody") is None
    with pytest.raises(ValueError):
        crm.create_client("  ")


def test_searches_go_on_top_of_the_board_and_move_between_columns():
    crm.create_client("Otter AI")
    crm.create_client("Acme")
    crm.assign_search("s1", "otter-ai")
    crm.assign_search("s2", "otter-ai")
    assert [p.session for p in crm.placements("otter-ai")] == ["s2", "s1"]  # newest first
    assert crm.client_of("s1") == "otter-ai" and crm.client_of("s3") is None

    crm.move_search("s1", "Won", ["s1"])
    crm.move_search("s2", "Won", ["s1", "s2"])
    (p1, p2) = crm.placements("otter-ai")
    assert (p1.session, p1.stage, p2.session, p2.stage) == ("s1", "Won", "s2", "Won")
    with pytest.raises(ValueError):
        crm.move_search("s1", "Nowhere")

    crm.assign_search("s1", "acme")  # another client's board: back to following its deals
    assert [p.session for p in crm.placements("acme")] == ["s1"] and crm.placements("acme")[0].stage is None
    assert {p.session for p in crm.placements()} == {"s1", "s2"}


def test_a_search_follows_its_deals_until_dragged():
    crm.create_client("Otter AI")
    crm.assign_search("s1", "otter-ai")
    (p,) = crm.placements("otter-ai")
    assert crm.search_stage(p, []) == "Sourcing"
    crm.ensure_deal("otter-ai", "s1", "acme.example", "Acme")
    assert crm.search_stage(p, crm.deals("otter-ai", session="s1")) == "Sourcing"
    crm.log("otter-ai", "s1", "acme.example", "Acme", "sent", contact="ceo@acme.example")
    assert crm.search_stage(p, crm.deals("otter-ai", session="s1")) == "Outreach"
    crm.log("otter-ai", "s1", "acme.example", "Acme", "reply", contact="ceo@acme.example")
    assert crm.search_stage(p, crm.deals("otter-ai", session="s1")) == "In talks"
    crm.move_search("s1", "Dropped")
    (p,) = crm.placements("otter-ai")
    assert crm.search_stage(p, crm.deals("otter-ai", session="s1")) == "Dropped"


def test_one_deal_per_client_and_company_across_searches():
    crm.create_client("Otter AI")
    crm.create_client("Acme")
    a = crm.ensure_deal("otter-ai", "s1", "holder.example", "Holder")
    b = crm.ensure_deal("otter-ai", "s2", "holder.example", "Holder")  # found again by a later search
    c = crm.ensure_deal("acme", "s3", "holder.example", "Holder")  # another client: its own deal
    assert a.id == b.id != c.id
    assert b.session == "s1" and b.stage == "To contact"
    assert [d.domain for d in crm.deals("otter-ai", ["holder.example", "other.example"])] == ["holder.example"]
    assert crm.deals("otter-ai", session="s2") == []  # counted under the search it was first worked from


def test_logging_moves_a_deal_up_never_back():
    crm.create_client("Otter AI")
    t0 = crm.now() - timedelta(days=10)
    sent = crm.log("otter-ai", "s1", "acme.example", "Acme", "sent", t0, contact="ceo@acme.example",
                   subject="Meeting recordings", body="Dear team")
    (deal,) = crm.deals("otter-ai")
    assert deal.stage == "Emailed" and deal.sent == 1 and deal.last_sent == t0
    assert deal.last_contact == "ceo@acme.example"
    assert sent.subject == "Meeting recordings" and sent.at == t0

    crm.log("otter-ai", "s1", "acme.example", "Acme", "reply", contact="ceo@acme.example", body="Tell me more",
            outcome="interested")
    crm.log("otter-ai", "s1", "acme.example", "Acme", "sent", contact="ceo@acme.example")  # a follow-up
    (deal,) = crm.deals("otter-ai")
    assert deal.stage == "Replied" and (deal.sent, deal.replies) == (2, 1)
    assert [a.kind for a in crm.timeline(deal.id)] == ["sent", "reply", "sent"]
    assert crm.timeline(deal.id)[1].outcome == "interested"

    crm.log("otter-ai", "s1", "acme.example", "Acme", "note", body="Prefers a call")
    assert crm.get_deal(deal.id).stage == "Replied"


def test_a_drag_wins_and_is_logged():
    crm.create_client("Otter AI")
    crm.log("otter-ai", "s1", "a.example", "A", "reply")
    crm.move_deal("otter-ai", "s1", "a.example", "A", "Dropped")
    crm.move_deal("otter-ai", "s1", "b.example", "B", "Dropped", ["b.example", "a.example"])  # no deal yet
    a, b = (crm.deals("otter-ai", [d])[0] for d in ("a.example", "b.example"))
    assert (a.stage, b.stage) == ("Dropped", "Dropped") and b.position < a.position
    assert crm.timeline(a.id)[0].body == "Replied → Dropped"
    crm.log("otter-ai", "s1", "a.example", "A", "reply")  # a late reply doesn't reopen it
    assert crm.get_deal(a.id).stage == "Dropped"
    crm.move_deal("otter-ai", "s1", "a.example", "A", "Dropped", ["a.example", "b.example"])  # reorder only
    assert [x.kind for x in crm.timeline(a.id)].count("stage") == 1


def test_recent_activity_stats_and_undo():
    crm.create_client("Otter AI")
    crm.log("otter-ai", "s1", "a.example", "A", "sent", crm.now() - timedelta(days=2))
    crm.log("otter-ai", "s1", "b.example", "B", "sent", crm.now() - timedelta(days=1))
    reply = crm.log("otter-ai", "s1", "b.example", "B", "reply")
    crm.log("otter-ai", "s1", "b.example", "B", "call")
    ((latest, company, search), *_) = crm.recent("otter-ai")
    assert (latest.kind, company, search) == ("call", "B", "s1")
    s = crm.stats(crm.deals("otter-ai"))
    assert (s.contacted, s.replied, s.calls) == (2, 1, 1)
    crm.delete_activity(reply.id)
    b = crm.deals("otter-ai", ["b.example"])[0]
    assert b.replies == 0 and b.stage == "Call scheduled"  # the stage stays where it got to
    assert crm.stats([]).last_activity is None
