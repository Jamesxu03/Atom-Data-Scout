"""Model and thinking settings to compare on the eval briefs (WS5). Live: uses the Gemini quota.

Run: uv run python -m eval.settings <setting> <brief> [runs]
  e.g. uv run python -m eval.settings C meeting_audio_en 2
Then: uv run python -m eval.discovery_quality meeting_audio_en

Each run is a session named "eval <brief> <setting> run <n>". Screen, triggers and confirmation
stay on Flash at low thinking in every setting: they read pages already fetched, and quote
checks guard their quality. Adopt a setting only if it raises confirmed good finds by at least
20% over A without lowering precision, and note the extra calls and minutes next to it. If
medium thinking doesn't raise the Google queries per discovery call, B is unlikely to find new
companies and can be dropped early. Grounded results vary between runs, so run each setting
several times: two runs of the same brief share only 30 to 40% of their relevant companies.
"""

import sys

from eval.discovery_quality import load_brief
from worker import config, pipeline, sessions
from worker.config import MODEL, PRO_MODEL

# setting -> step -> (model, thinking); steps not named keep config's defaults
SETTINGS = {
    "A": {"hypotheses": (MODEL, "medium"), "discovery": (MODEL, "low")},  # today's baseline
    "B": {"hypotheses": (MODEL, "medium"), "discovery": (MODEL, "medium")},
    "C": {"hypotheses": (PRO_MODEL, "medium"), "discovery": (MODEL, "low")},
    "D": {"hypotheses": (PRO_MODEL, "medium"), "discovery": (MODEL, "medium")},
}


def apply(name: str) -> None:
    """Set the steps of one setting in config, which every Gemini call reads (llm.step)."""
    for step, (model, thinking) in SETTINGS[name].items():
        config.STEP_MODELS[step] = model
        config.STEP_THINKING[step] = thinking


def run(name: str, brief_name: str, runs: int = 2) -> list[sessions.Session]:
    apply(name)
    out = []
    for i in range(1, runs + 1):
        s = sessions.create(f"eval {brief_name} {name} run {i}", load_brief(brief_name))
        out.append(pipeline.search(s))
    return out


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in SETTINGS:
        sys.exit(__doc__)
    for s in run(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 2):
        print(f"{s.path.name}: {s.meta.stage}, {s.meta.timings.get('total', 0):.0f}s")
