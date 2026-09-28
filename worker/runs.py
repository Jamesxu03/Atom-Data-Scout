"""Each pipeline run writes its step outputs as JSON under data/runs/<timestamp>/."""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

RUNS_DIR = Path(__file__).resolve().parent.parent / "data" / "runs"


def new_run() -> Path:
    path = RUNS_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
    path.mkdir(parents=True, exist_ok=True)
    return path


def latest_run() -> Path:
    runs = sorted(p for p in RUNS_DIR.glob("*") if p.is_dir())
    if not runs:
        raise FileNotFoundError("No runs yet — run worker.steps.parse_brief first.")
    return runs[-1]


def save(run: Path, name: str, obj: BaseModel) -> Path:
    path = run / f"{name}.json"
    path.write_text(obj.model_dump_json(indent=2), encoding="utf-8")
    return path


def load[T: BaseModel](run: Path, name: str, schema: type[T]) -> T:
    return schema.model_validate_json((run / f"{name}.json").read_text(encoding="utf-8"))
