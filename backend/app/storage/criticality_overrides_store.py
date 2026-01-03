import json
from pathlib import Path
from typing import Dict, Optional

BASE = Path("data/criticality_overrides")
BASE.mkdir(parents=True, exist_ok=True)


def _path(workload_id: str) -> Path:
    return BASE / f"{workload_id}.json"


def load_criticality_overrides(workload_id: str) -> Dict[str, int]:
    """Load node criticality score overrides. Returns {node_id: score}."""
    path = _path(workload_id)
    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text())
        return {k: v for k, v in raw.items() if isinstance(v, int) and 1 <= v <= 10}
    except Exception:
        return {}


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

    path = _path(workload_id)
    with path.open("w") as f:
        json.dump(overrides, f, indent=2)

    return True


def delete_criticality_override(workload_id: str, node_id: str) -> bool:
    """Delete criticality override for a node. Returns True if deleted."""
    overrides = load_criticality_overrides(workload_id)
    if node_id not in overrides:
        return False

    overrides.pop(node_id, None)
    path = _path(workload_id)
    with path.open("w") as f:
        json.dump(overrides, f, indent=2)

    return True
