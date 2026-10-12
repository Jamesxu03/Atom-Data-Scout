"""Discovery: search channels in parallel, in rounds, check sources, merge and dedupe by domain.

Channels (channels.py), each one grounded Gemini call:
  ready sources     (d) marketplaces, collection services and published datasets, (e) open
                    corpora and public bodies
  untapped holders  (a) product search from the holder's side, (c) directories (vendors, as a
                    way to their customers), (f) distressed, precedent and regulated
                    institutions where something changed, (g) signals on their own pages,
                    (h) customer stories of the tools they run on, one call per tool, (i) public
                    records (tender buyers and suppliers, research projects, registers)
  (b) snowball      organisations similar to the best holders, once after the last round

Rounds (rounds.py): round 1 runs every channel for the source types the brief wants (E1) plus
each local market's native-language searches; up to two more rounds rerun only what found new
qualifying companies, told what is already found. check_sources() confirms source pages before
screening, which reads them too (core.py). Domains in the brief's exclusions never become
candidates.
"""

from worker.discovery import channels
from worker.discovery.core import (
    BUILT_BY_MERGE,
    Discovery,
    Hit,
    Task,
    check_sources,
    merge,
    run_tasks,
    slot,
)
from worker.discovery.rounds import (
    discover,
    final_tasks,
    finish_round,
    next_calls,
    pick_seeds,
    qualifies,
    round_one_calls,
    run_round,
    seed_types,
    snowball,
    uncovered_archetypes,
)

__all__ = [
    "BUILT_BY_MERGE", "Discovery", "Hit", "Task", "channels", "check_sources", "discover", "final_tasks",
    "finish_round", "merge", "next_calls", "pick_seeds", "qualifies", "round_one_calls", "run_round", "run_tasks",
    "seed_types", "slot", "snowball", "uncovered_archetypes",
]
