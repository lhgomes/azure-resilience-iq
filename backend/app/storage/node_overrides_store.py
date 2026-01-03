import json
from pathlib import Path
from typing import Dict

from app.intent.node_override import NodeOverride

BASE = Path("data/node_overrides")
BASE.mkdir(parents=True, exist_ok=True)


def _path(workload_id: str) -> Path:
    return BASE / f"{workload_id}.json"


def load_node_overrides(workload_id: str) -> Dict[str, NodeOverride]:
    path = _path(workload_id)
    if not path.exists():
        return {}

    raw = json.loads(path.read_text())
    return {k: NodeOverride(**v) for k, v in raw.items()}


def save_node_override(workload_id: str, override: NodeOverride):
    overrides = load_node_overrides(workload_id)
    overrides[override.node_id] = override

    path = _path(workload_id)
    with path.open("w") as f:
        json.dump({k: v.model_dump() for k, v in overrides.items()}, f, indent=2)


def delete_node_override(workload_id: str, node_id: str) -> bool:
    overrides = load_node_overrides(workload_id)
    if node_id not in overrides:
        return False

    overrides.pop(node_id, None)
    path = _path(workload_id)
    with path.open("w") as f:
        json.dump({k: v.model_dump() for k, v in overrides.items()}, f, indent=2)

    return True
