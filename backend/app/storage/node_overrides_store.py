from pathlib import Path
from typing import Dict

from app.intent.node_override import NodeOverride
from app.storage._json_repo import read_json, write_json
from app.config import get_node_overrides_path


def _path(subscription_id: str) -> Path:
    return get_node_overrides_path(subscription_id)


def load_node_overrides(subscription_id: str) -> Dict[str, NodeOverride]:
    raw = read_json(_path(subscription_id), default={})
    if not isinstance(raw, dict):
        return {}
    return {k: NodeOverride(**v) for k, v in raw.items() if isinstance(v, dict)}


def save_node_override(subscription_id: str, override: NodeOverride):
    overrides = load_node_overrides(subscription_id)
    overrides[override.node_id] = override

    write_json(_path(subscription_id), {k: v.model_dump() for k, v in overrides.items()})


def delete_node_override(subscription_id: str, node_id: str) -> bool:
    overrides = load_node_overrides(subscription_id)
    if node_id not in overrides:
        return False

    overrides.pop(node_id, None)
    write_json(_path(subscription_id), {k: v.model_dump() for k, v in overrides.items()})

    return True
