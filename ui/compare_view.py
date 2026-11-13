"""The Compare tab works on a working brief and working kills, kept in st.session_state per
session. Everything shown is a preview re-scored in Python (worker/compare.py); nothing is
written until Save, which creates a new session and leaves this one as it is."""

from pathlib import Path

import pandas as pd
import streamlit as st

from ui.brief_form import category_input
from ui.company_view import contacts_section, research_details
from ui.labels import (
    FACT_LABELS,
    LEGAL_NOTE,
    LIST_TITLES,
    QUESTION_LABELS,
    VERDICT_ICONS,
    change_text,
    fact_text,
    fmt,
    kill_text,
    obstacle_text,
    rank_text,
    type_label,
)
from ui.nav import open_session
from worker import compare, crm, sessions
from worker.brief_template import CATEGORIES, Brief, Pick
from worker.schemas import Candidate, CheckedCategory, Evidence
from worker.scoring import answers, must_failures, saleability_label

MAX_COMPARED = 4
NONE = "—"
DIFFERENT = "background-color: rgba(255, 196, 0, 0.18)"
PROVISIONAL = "color: #8a8a8a; font-style: italic"


def _cmp(path: str, name: str) -> str:
    return f"cmp-{Path(path).name}-{name}"


def reset_working(path: str) -> None:
    """Forget the working brief, kills and picks: back to what is saved."""
    for k in [k for k in st.session_state if str(k).startswith(_cmp(path, ""))]:
        del st.session_state[k]


def _rebuild_editor(path: str, brief: Brief, shown: list[str]) -> None:
    """New editor widgets, filled in from brief."""
    st.session_state[_cmp(path, "snapshot")] = brief
    st.session_state[_cmp(path, "shown")] = shown
    st.session_state[_cmp(path, "version")] += 1


def _remove_requirement(path: str, key: str) -> None:
    brief = st.session_state[_cmp(path, "brief")]
    picks = {k: p for k, p in brief.picks.items() if k != key}
    _rebuild_editor(path, brief.model_copy(update={"picks": picks}),
                    [k for k in st.session_state[_cmp(path, "shown")] if k != key])


def _add_requirement(path: str, widget: str) -> None:
    if key := st.session_state.get(widget):
        _rebuild_editor(path, st.session_state[_cmp(path, "brief")], [*st.session_state[_cmp(path, "shown")], key])


def _kill(path: str, domain: str) -> None:
    reason = st.session_state.get(_cmp(path, f"why-{domain}"), "").strip()
    st.session_state[_cmp(path, "kills")] = {**st.session_state[_cmp(path, "kills")], domain: reason}
    pick = _cmp(path, "pick")
    st.session_state[pick] = [d for d in st.session_state.get(pick, []) if d != domain]


def _restore(path: str, domain: str) -> None:
    st.session_state[_cmp(path, "kills")] = {d: why for d, why in st.session_state[_cmp(path, "kills")].items()
                                             if d != domain}


def _pick(path: str, domains: list[str]) -> None:
    st.session_state[_cmp(path, "pick")] = domains


def _save(path: str) -> None:
    s = sessions.load(path)
    p = compare.preview(s, st.session_state[_cmp(path, "brief")], st.session_state[_cmp(path, "kills")])
    saved = compare.save(s, p, st.session_state.get(_cmp(path, "name"), "").strip() or s.meta.name)
    if client_id := crm.client_of(Path(path).name):  # on the same client's board
        crm.assign_search(saved.path.name, client_id)
    reset_working(path)
    open_session(str(saved.path))


def compare_tab(s: sessions.Session, path: str) -> None:
    if not any(c.research for c in s.discovery.candidates):
        st.caption("Only researched companies are compared: research some under Candidates first.")
        return
    kills = st.session_state.setdefault(_cmp(path, "kills"), compare.saved_kills(s))
    left, right = st.columns([1, 3], gap="large")
    with left:
        brief = brief_editor(s, path)
    p = compare.preview(s, brief, kills)
    with right:
        save_bar(s, path, p)
        domains = picker(p, path)
        side_by_side(p, domains, path)
        killed_list(p, path)
        overview(p)
        st.caption(f"Fit change and rank are against the original search. {LEGAL_NOTE}")


def brief_editor(s: sessions.Session, path: str) -> Brief:
    """The working brief: edit, remove or add requirements. Returns it, and keeps it in state."""
    snapshot = st.session_state.setdefault(_cmp(path, "snapshot"), s.brief)
    shown = st.session_state.setdefault(_cmp(path, "shown"), [k for k in CATEGORIES if s.brief.is_set(k)])
    fid = f"{_cmp(path, 'form')}-{st.session_state.setdefault(_cmp(path, 'version'), 0)}"
    st.markdown("**Working brief**")
    st.caption("Edit, remove or add requirements: the comparison follows. Nothing is saved until you click Save.")
    with st.expander("What data and notes"):
        description = st.text_input("What data, in a few words", snapshot.description, key=f"{fid}-description")
        notes = st.text_area("Notes", snapshot.notes, key=f"{fid}-notes")
    picks: dict[str, Pick] = {}
    flags = {}
    for key in (k for k in CATEGORIES if k in shown):
        with st.container(border=True):
            picks[key] = category_input(CATEGORIES[key], snapshot, fid, picks)
            flags[key] = st.empty()
            st.button("Remove", key=f"{fid}-{key}-remove", icon=":material/close:", type="tertiary",
                      on_click=_remove_requirement, args=(path, key))
    st.selectbox("Add a requirement", [k for k in CATEGORIES if k not in shown], index=None, key=f"{fid}-add",
                 format_func=lambda k: f"{CATEGORIES[k].code} {CATEGORIES[k].label}", placeholder="Pick a category",
                 on_change=_add_requirement, args=(path, f"{fid}-add"))
    brief = Brief(description=description.strip() or snapshot.description, notes=notes,
                  picks={k: p for k, p in picks.items() if p.values or p.amount is not None or p.amount_to is not None})
    st.session_state[_cmp(path, "brief")] = brief
    for ch in compare.changes(s.brief, brief):
        if ch.key in flags and not ch.exact:
            flags[ch.key].caption("Judged against the old brief: fits that rest on it are provisional (*)")
    return brief


def save_bar(s: sessions.Session, path: str, p: compare.Preview) -> None:
    if not p.unsaved:
        st.caption("No unsaved changes. Edit the brief or kill companies to compare them another way.")
        return
    with st.container(border=True):
        st.warning("**Unsaved changes.** This is a preview: Save keeps it as a new session, and this session stays "
                   "as it is.")
        name, save, reset = st.columns([3, 1, 1], vertical_alignment="bottom")
        name.text_input("Save as", s.meta.name if s.meta.parent else f"{s.meta.name} · compare",
                        key=_cmp(path, "name"))
        save.button("Save", type="primary", on_click=_save, args=(path,), width="stretch")
        reset.button("Reset", on_click=reset_working, args=(path,), width="stretch",
                     help="Back to this session's brief and kills")


def pick_label(c: Candidate) -> str:
    return " · ".join(x for x in (c.name, type_label(c), ", ".join(c.regions)) if x)


def picker(p: compare.Preview, path: str) -> list[str]:
    """Search the researched companies of one type and pick up to MAX_COMPARED: ready sources and
    untapped holders are judged differently, so they are never compared with each other. Values
    are domains, so a pick survives re-scoring."""
    alive = {c.domain: c for c in p.candidates if not c.killed}
    kinds = list(dict.fromkeys(c.holder_type for c in alive.values()))
    if len(kinds) > 1:
        kind = st.radio("Compare", kinds, horizontal=True, key=_cmp(path, "type"), format_func=LIST_TITLES.get,
                        help="Ready sources and untapped holders are compared separately")
        alive = {d: c for d, c in alive.items() if c.holder_type == kind}
    key = _cmp(path, "pick")
    saved = st.session_state.get(key)
    chosen = [d for d in saved if d in alive] if saved is not None else list(alive)[:MAX_COMPARED]
    if saved and not chosen:  # the picks were of the other type: start this one from its best
        chosen = list(alive)[:MAX_COMPARED]
    st.session_state[key] = chosen
    search, top = st.columns([4, 1], vertical_alignment="bottom")
    query = search.text_input("Search researched companies", key=_cmp(path, "query"),
                              placeholder="Name, type, region, language or what data they hold")
    top.button(f"Top {MAX_COMPARED}", on_click=_pick, args=(path, list(alive)[:MAX_COMPARED]), width="stretch",
               help="The best fits of this type under the working brief")
    found = [d for d, c in alive.items() if d not in chosen and compare.matches(c, query)]
    picked = st.multiselect(f"Compare (up to {MAX_COMPARED})", chosen + found, key=key, max_selections=MAX_COMPARED,
                            format_func=lambda d: pick_label(alive[d]), placeholder="Pick companies to compare")
    if query and not found:
        st.caption("No other researched company matches.")
    return picked


def answer_text(c: Candidate, q: str, brief: Brief) -> str:
    if c.holder_type != "untapped":
        return NONE
    a, score = (s := c.current_saleability()) and getattr(s, q), answers(c, brief)[q]
    if score is None:
        return "?"
    return f"{score}/5 ({'verified' if a and a.verified else 'initial'})"


def verdict_text(c: Candidate, key: str) -> str:
    ch = next((ch for ch in c.research.checks if ch.category == key), None)
    return f"{VERDICT_ICONS[ch.verdict]} {ch.verdict}" if ch else NONE


def compared_rows(cands: list[Candidate], p: compare.Preview) -> dict[str, list[str]]:
    """Short values, one row per field: these are compared and highlighted."""
    checks = [k for k in CheckedCategory.__args__ if any(ch.category == k for c in cands for ch in c.research.checks)]
    rows = {
        "Fit": [fmt(c.fit) + ("*" if c.domain in p.provisional else "") for c in cands],
        "Provisional because": ["; ".join(p.provisional.get(c.domain, [])) or NONE for c in cands],
        "Fit change": [change_text(c.fit, p.fit_before.get(c.domain)) or NONE for c in cands],
        "Rank": [rank_text(p.rank_before.get(c.domain), p.rank_now.get(c.domain)) for c in cands],
        "Type": [type_label(c) for c in cands],
        "Saleability": [saleability_label(c, p.brief) for c in cands],
        "Licence found": [c.research.licence if c.holder_type == "ready" else NONE for c in cands],
        **{label: [answer_text(c, q, p.brief) for c in cands] for q, label in QUESTION_LABELS.items()},
        **{CATEGORIES[k].label: [verdict_text(c, k) for c in cands] for k in checks},
        "Must-haves not met": [", ".join(CATEGORIES[k].label for k in must_failures(c, p.brief)) or NONE
                               for c in cands],
        "Languages": [", ".join(c.languages) or NONE for c in cands],
        "Regions": [", ".join(c.regions) or NONE for c in cands],
        "Verified contacts": [str(sum(k.verified for k in c.contacts)) for c in cands],
        "Email": ["drafted" if c.email else NONE for c in cands],
    }
    return {k: v for k, v in rows.items() if any(x != NONE for x in v)}


def text_rows(cands: list[Candidate]) -> dict[str, list[str]]:
    """Free text: rarely the same twice, so always shown."""
    checks = [k for k in CheckedCategory.__args__ if any(ch.category == k for c in cands for ch in c.research.checks)]
    rows = {
        "Data held": [c.research.data_held for c in cands],
        "Volume found": [c.research.volume_estimate or "?" for c in cands],
        **{label: [fact_text(c, k) for c in cands] for k, label in FACT_LABELS.items()},
        **{f"{CATEGORIES[k].label}: finding": [
            next((ch.finding for ch in c.research.checks if ch.category == k), NONE) for c in cands] for k in checks},
        "Obstacle → how to buy": [obstacle_text(c) or NONE for c in cands],
        "Why it was found": [c.reason or NONE for c in cands],
    }
    return {k: v for k, v in rows.items() if any(x != NONE for x in v)}


def quotes(c: Candidate) -> list[tuple[str, Evidence]]:
    r, s = c.research, c.current_saleability()
    out = [("Licence", ev) for ev in r.licence_evidence]
    out += [(label, ev) for q, label in QUESTION_LABELS.items() for ev in ((s and getattr(s, q).evidence) or [])]
    out += [("Volume", ev) for ev in r.volume_evidence]
    out += [(label, f) for k, label in FACT_LABELS.items() if (f := c.fact(k))]
    out += [(CATEGORIES[ch.category].label, ev) for ch in r.checks for ev in ch.evidence]
    return out


def _table(rows: dict[str, list[str]], names: list[str], provisional: list[bool] = ()) -> None:
    """Fields down, companies across. Rows where companies differ are highlighted; a
    provisional fit is greyed."""
    df = pd.DataFrame.from_dict(rows, orient="index", columns=names)

    def style(row: pd.Series) -> list[str]:
        differ = len(names) > 1 and row.nunique() > 1
        out = [DIFFERENT if differ else "" for _ in row]
        if row.name == "Fit":
            out = [f"{o};{PROVISIONAL}" if prov else o for o, prov in zip(out, provisional, strict=True)]
        return out

    st.dataframe(df.style.apply(style, axis=1), width="stretch")


def side_by_side(p: compare.Preview, domains: list[str], path: str) -> None:
    cands = [p.get(d) for d in domains]
    if not cands:
        st.caption("Pick companies above to compare them side by side.")
        return
    for col, c in zip(st.columns(len(cands)), cands, strict=True):
        with col:
            st.markdown(f"**{c.name}**  \n[{c.domain}]({c.website})")
            kill, details = st.columns(2)
            with kill.popover("Kill", icon=":material/block:", width="stretch", key=_cmp(path, f"kill-{c.domain}")):
                st.text_input("Why", key=_cmp(path, f"why-{c.domain}"), placeholder="e.g. licence too strict")
                st.button("Kill", key=_cmp(path, f"kill-ok-{c.domain}"), type="primary", on_click=_kill,
                          args=(path, c.domain), help="Until you save, only this preview changes")
            if details.button("Details", key=_cmp(path, f"details-{c.domain}"), width="stretch"):
                details_dialog(c, p.brief, path)
    names = [c.name if [x.name for x in cands].count(c.name) == 1 else f"{c.name} ({c.domain})" for c in cands]
    only_diff = st.toggle("Only differences", key=_cmp(path, "diff"), disabled=len(cands) < 2,
                          help="Hides rows where every company has the same value. Text rows always show.")
    rows = compared_rows(cands, p)
    if only_diff and len(cands) > 1:
        rows = {k: v for k, v in rows.items() if len(set(v)) > 1 or k == "Fit"}
    _table(rows, names, [c.domain in p.provisional for c in cands])
    if any(c.domain in p.provisional for c in cands):
        st.caption("\\* Provisional: this fit rests on something Gemini judged against an older brief (see "
                   "*Provisional because*). Research the company again for an exact score.")
    with st.expander("Text and sources"):
        _table(text_rows(cands), names)
        for col, c in zip(st.columns(len(cands)), cands, strict=True):
            with col:
                st.markdown(f"**{c.name}**")
                for what, ev in (found := quotes(c)):
                    st.markdown(f"*{what}:* {ev.quote}\n\n[{ev.url}]({ev.url})")
                if not found:
                    st.caption("No verified quotes.")


@st.dialog("Research details", width="large")
def details_dialog(c: Candidate, brief: Brief, path: str) -> None:
    st.subheader(c.name)
    st.caption("Scored under the working brief.")
    research_details(c, brief)
    contacts_section(c, path)


def killed_list(p: compare.Preview, path: str) -> None:
    dead = [c for c in p.candidates if c.killed]
    if not dead:
        return
    with st.expander(f"Killed ({len(dead)})"):
        for c in dead:
            text, button = st.columns([4, 1], vertical_alignment="center")
            text.markdown(f"**{c.name}** — {c.kill_reason or 'no reason given'}")
            excluded = c.kill_reason == compare.EXCLUDED
            button.button("Restore", key=_cmp(path, f"restore-{c.domain}"), on_click=_restore, args=(path, c.domain),
                          disabled=excluded, width="stretch",
                          help="Remove it from the brief's exclusions to restore it" if excluded else None)


def overview(p: compare.Preview) -> None:
    with st.expander(f"All researched ({len(p.candidates)})"):
        checks = [k for k in CheckedCategory.__args__
                  if any(ch.category == k for c in p.candidates for ch in c.research.checks)]
        st.dataframe(pd.DataFrame([
            {
                "Rank": rank_text(p.rank_before.get(c.domain), p.rank_now.get(c.domain)),
                "Fit": fmt(c.fit) + ("*" if c.domain in p.provisional else ""),
                "Fit change": change_text(c.fit, p.fit_before.get(c.domain)),
                "Company": c.name,
                "Type": type_label(c),
                "Saleability": saleability_label(c, p.brief),
                "Must-haves not met": ", ".join(CATEGORIES[k].label for k in must_failures(c, p.brief)),
                "Volume found": c.research.volume_estimate or "?",
                "Price": fact_text(c, "price"),
                "Data years": fact_text(c, "data_period"),
                **{CATEGORIES[k].label: verdict_text(c, k) for k in checks},
                "Obstacle → how to buy": obstacle_text(c),
                "Verified contacts": sum(k.verified for k in c.contacts),
                "Email": "drafted" if c.email else "",
                "Killed": kill_text(c) if c.killed else "",
            }
            for c in p.candidates
        ]), hide_index=True, width="stretch")
