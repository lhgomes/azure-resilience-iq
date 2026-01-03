import json
from pathlib import Path
from app.intent.overrides import EdgeOverride

BASE = Path("data/overrides")
BASE.mkdir(parents=True, exist_ok=True)


def _path(workload_id: str) -> Path:
    return BASE / f"{workload_id}.json"


def load_overrides(workload_id: str) -> dict[str, EdgeOverride]:
    path = _path(workload_id)
    if not path.exists():
        return {}

    raw = json.loads(path.read_text())
    return {
        k: EdgeOverride(**v)
        for k, v in raw.items()
    }


def save_override(workload_id: str, override: EdgeOverride):
    overrides = load_overrides(workload_id)
    overrides[override.edge_id] = override

    path = _path(workload_id)
    with path.open("w") as f:
        json.dump(
            {k: v.model_dump() for k, v in overrides.items()},
            f,
            indent=2
        )
