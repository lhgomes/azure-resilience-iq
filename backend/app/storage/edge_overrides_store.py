from pathlib import Path
from app.intent.overrides import EdgeOverride
from app.storage._json_repo import read_json, write_json
from app.config import get_edge_overrides_path


def _path(subscription_id: str) -> Path:
    return get_edge_overrides_path(subscription_id)


def load_overrides(subscription_id: str) -> dict[str, EdgeOverride]:
    raw = read_json(_path(subscription_id), default={})
    if not isinstance(raw, dict):
        return {}
    return {
        k: EdgeOverride(**v)
        for k, v in raw.items()
        if isinstance(v, dict)
    }


def save_override(subscription_id: str, override: EdgeOverride):
    overrides = load_overrides(subscription_id)
    overrides[override.edge_id] = override

    write_json(_path(subscription_id), {k: v.model_dump() for k, v in overrides.items()})
