from pathlib import Path
from app.intent.overrides import EdgeOverride
from app.storage._json_repo import DATA_DIR, read_json, write_json

BASE = DATA_DIR / "edge_overrides"


def _path(workload_id: str) -> Path:
    return BASE / f"{workload_id}.json"


def load_overrides(workload_id: str) -> dict[str, EdgeOverride]:
    raw = read_json(_path(workload_id), default={})
    if not isinstance(raw, dict):
        return {}
    return {
        k: EdgeOverride(**v)
        for k, v in raw.items()
        if isinstance(v, dict)
    }


def save_override(workload_id: str, override: EdgeOverride):
    overrides = load_overrides(workload_id)
    overrides[override.edge_id] = override

    write_json(_path(workload_id), {k: v.model_dump() for k, v in overrides.items()})
