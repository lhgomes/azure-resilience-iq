from pathlib import Path
from typing import Dict, Optional

from app.storage._json_repo import DATA_DIR, read_json, write_json

BASE = DATA_DIR / "criticality_overrides"


def _path(workload_id: str) -> Path:
    return BASE / f"{workload_id}.json"


def load_criticality_overrides(workload_id: str) -> Dict[str, int]:
    """Load node criticality score overrides. Returns {node_id: score}."""
    raw = read_json(_path(workload_id), default={})
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, int) and 1 <= v <= 10}


def get_criticality_override(workload_id: str, node_id: str) -> Optional[int]:
    """Get override for a single node, or None if not overridden."""
    overrides = load_criticality_overrides(workload_id)
    return overrides.get(node_id)


def save_criticality_override(workload_id: str, node_id: str, score: int) -> bool:
    """Save a criticality score override. Score must be 1-10. Returns True if saved."""
    if not isinstance(score, int) or score < 1 or score > 10:
        return False

    overrides = load_criticality_overrides(workload_id)
    overrides[node_id] = score

    write_json(_path(workload_id), overrides)

    return True


def delete_criticality_override(workload_id: str, node_id: str) -> bool:
    """Delete criticality override for a node. Returns True if deleted."""
    overrides = load_criticality_overrides(workload_id)
    if node_id not in overrides:
        return False

    overrides.pop(node_id, None)
    write_json(_path(workload_id), overrides)

    return True
