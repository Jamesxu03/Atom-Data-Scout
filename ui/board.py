"""A kanban board: columns of cards, dragged between columns (or within one) and clicked to open.
An inline st.components.v2 component, so no build step and no extra package.

Everything shown comes from crawled pages, Gemini or the user, so the script builds the cards
with textContent and never innerHTML. Colours come from the theme's --st-* variables, so the board
follows the light or dark theme. Drags only report back: the page saves the move and reruns.
"""

from dataclasses import asdict, dataclass, field
from typing import Literal

import streamlit as st

Tone = Literal["", "warn", "good", "muted"]


@dataclass
class Card:
    id: str
    column: str
    title: str
    lines: list[str] = field(default_factory=list)
    badge: str = ""  # a short number in a circle, e.g. the fit
    badge_help: str = ""
    chip: str = ""  # e.g. "✉ 3 days ago"
    tone: Tone = ""  # the chip's colour: warn when a follow-up is due


@dataclass
class Move:
    id: str
    column: str
    order: list[str]  # the ids of the drop column, top to bottom, after the drop


CSS = """
.board { --accent: var(--st-primary-color, #FF5A6A); --text: var(--st-text-color, currentColor);
  --line: color-mix(in srgb, var(--text) 14%, transparent); --mute: color-mix(in srgb, var(--text) 62%, transparent);
  display: flex; gap: 12px; overflow-x: auto; padding: 2px 2px 8px; color: var(--text);
  font-family: var(--st-font, inherit); -webkit-overflow-scrolling: touch; }
.col { flex: 1 0 180px; max-width: 320px; display: flex; flex-direction: column; border-radius: 12px; max-height: 68vh;
  background: var(--st-secondary-background-color, color-mix(in srgb, var(--text) 5%, transparent));
  border: 1px solid transparent; }
.col.over { border-color: var(--accent); }
.head { padding: 10px 12px 6px; font-weight: 600; font-size: 0.92rem; display: flex; justify-content: space-between; }
.count { color: var(--mute); font-weight: 400; }
.cards { flex: 1; overflow-y: auto; padding: 4px 8px 8px; display: flex; flex-direction: column; gap: 8px;
  min-height: 56px; }
.card { background: var(--st-background-color, transparent); border: 1px solid var(--line); border-radius: 10px;
  padding: 10px 12px; cursor: grab; user-select: none; }
.card:hover { border-color: color-mix(in srgb, var(--accent) 60%, transparent); }
.card:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.card.dragging { opacity: 0.45; }
.top { display: flex; align-items: flex-start; gap: 8px; }
.title { flex: 1; font-weight: 600; color: var(--accent); font-size: 0.95rem; line-height: 1.3;
  overflow-wrap: anywhere; }
.badge { flex: none; min-width: 30px; height: 30px; border-radius: 50%; border: 2px solid #f0b429;
  display: grid; place-items: center; font-size: 0.78rem; font-weight: 700; }
.line { color: var(--mute); font-size: 0.82rem; margin-top: 4px; line-height: 1.35; overflow-wrap: anywhere; }
.chip { display: inline-block; margin-top: 8px; font-size: 0.78rem; padding: 2px 8px; border-radius: 999px;
  border: 1px solid var(--line); }
.chip.warn { border-color: #f0b429; background: color-mix(in srgb, #f0b429 16%, transparent); }
.chip.good { border-color: #2f9e6f; background: color-mix(in srgb, #2f9e6f 16%, transparent); }
.chip.muted { color: var(--mute); }
.foot { padding: 6px 12px 10px; font-size: 0.8rem; color: var(--mute); border-top: 1px solid var(--line); }
.empty { color: var(--mute); font-size: 0.82rem; text-align: center; padding: 12px 4px; }
@media (max-width: 640px) { .col { flex-basis: 78vw; } }
"""

JS = """
export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null && text !== '') e.textContent = String(text);
    return e;
  };
  const old = parentElement.querySelector('.board');
  if (old) old.remove();
  const board = el('div', 'board');
  parentElement.appendChild(board);
  let dragged = null;
  // near the board's left or right edge while dragging, scroll it: columns past the edge can be reached
  const EDGE = 64;
  board.addEventListener('dragover', (e) => {
    if (!dragged) return;
    const r = board.getBoundingClientRect();
    if (e.clientX < r.left + EDGE) board.scrollLeft -= 18;
    else if (e.clientX > r.right - EDGE) board.scrollLeft += 18;
  });

  const idsIn = (list) => [...list.querySelectorAll('.card')].map((c) => c.dataset.id);
  const after = (list, y) => {
    const rest = [...list.querySelectorAll('.card:not(.dragging)')];
    return rest.find((c) => { const r = c.getBoundingClientRect(); return y < r.top + r.height / 2; }) || null;
  };

  for (const name of data.columns) {
    const cards = data.cards.filter((c) => c.column === name);
    const col = el('section', 'col');
    col.setAttribute('aria-label', name);
    const head = el('div', 'head');
    head.append(el('span', '', name), el('span', 'count', cards.length));
    const list = el('div', 'cards');
    list.dataset.column = name;
    if (!cards.length) list.append(el('div', 'empty', data.empty || 'Drop here'));
    for (const c of cards) {
      const card = el('div', 'card');
      card.draggable = true;
      card.tabIndex = 0;
      card.dataset.id = c.id;
      card.setAttribute('role', 'button');
      const top = el('div', 'top');
      top.append(el('div', 'title', c.title));
      if (c.badge) { const b = el('div', 'badge', c.badge); if (c.badge_help) b.title = c.badge_help; top.append(b); }
      card.append(top);
      for (const line of c.lines) card.append(el('div', 'line', line));
      if (c.chip) card.append(el('div', 'chip ' + (c.tone || ''), c.chip));
      card.addEventListener('click', () => setTriggerValue('open', c.id));
      card.addEventListener('keydown', (e) => { if (e.key === 'Enter') setTriggerValue('open', c.id); });
      card.addEventListener('dragstart', (e) => {
        dragged = card; card.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', c.id);
      });
      card.addEventListener('dragend', () => { card.classList.remove('dragging'); dragged = null; });
      list.append(card);
    }
    col.addEventListener('dragover', (e) => {
      if (!dragged) return;
      e.preventDefault(); col.classList.add('over');
      const empty = list.querySelector('.empty'); if (empty) empty.remove();
      const next = after(list, e.clientY);
      if (next) list.insertBefore(dragged, next); else list.append(dragged);
    });
    col.addEventListener('dragleave', (e) => { if (!col.contains(e.relatedTarget)) col.classList.remove('over'); });
    col.addEventListener('drop', (e) => {
      e.preventDefault(); col.classList.remove('over');
      if (!dragged) return;
      setTriggerValue('move', { id: dragged.dataset.id, column: name, order: idsIn(list) });
    });
    col.append(head, list);
    const foot = (data.footers || {})[name];
    if (foot) col.append(el('div', 'foot', foot));
    board.append(col);
  }
}
"""

def board(columns: list[str], cards: list[Card], key: str, footers: dict[str, str] | None = None,
          empty: str = "Drop here") -> tuple[Move | None, str | None]:
    """Show the board. Returns the move just made, if any, and the id of a card just clicked."""
    # registered on each call, not once on import: the registry belongs to the runtime, which a test
    # (AppTest) starts afresh on every run. Registering the same definition again is a no-op.
    kanban = st.components.v2.component("atom_kanban", css=CSS, js=JS)
    result = kanban(
        key=key,
        data={"columns": columns, "cards": [asdict(c) for c in cards], "footers": footers or {}, "empty": empty},
        on_move_change=lambda: None,
        on_open_change=lambda: None,
    )
    move, opened = result.get("move"), result.get("open")
    if isinstance(move, dict) and move.get("id") and move.get("column") in columns:
        return Move(id=str(move["id"]), column=move["column"], order=[str(i) for i in move.get("order", [])]), None
    return None, str(opened) if opened else None
