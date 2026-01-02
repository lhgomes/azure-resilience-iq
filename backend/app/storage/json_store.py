import json
from pathlib import Path

BASE = Path("data")
BASE.mkdir(exist_ok=True)


def save_snapshot(workload_id: str, snapshot: dict):
    path = BASE / f"{workload_id}.json"
    with path.open("w") as f:
        json.dump(snapshot, f, indent=2, default=str)


def load_snapshot(workload_id: str) -> dict | None:
    path = BASE / f"{workload_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())
