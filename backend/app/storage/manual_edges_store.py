import json
from pathlib import Path
from typing import List

from app.intent.manual_edge import ManualEdge

BASE = Path("data/manual_edges")
BASE.mkdir(parents=True, exist_ok=True)


def _path(workload_id: str) -> Path:
    return BASE / f"{workload_id}.json"


def load_manual_edges(workload_id: str) -> List[ManualEdge]:
    path = _path(workload_id)
    if not path.exists():
        return []

    raw = json.loads(path.read_text())
    return [ManualEdge(**item) for item in raw]


def save_manual_edge(workload_id: str, edge: ManualEdge):
    edges = load_manual_edges(workload_id)

    # dedupe by id so the latest write wins
    edges = [e for e in edges if e.id != edge.id]
    edges.append(edge)

    path = _path(workload_id)
    with path.open("w") as f:
        json.dump([e.model_dump() for e in edges], f, indent=2)


def delete_manual_edge(workload_id: str, edge_id: str) -> bool:
    edges = load_manual_edges(workload_id)
    new_edges = [e for e in edges if e.id != edge_id]

    if len(new_edges) == len(edges):
        return False

    path = _path(workload_id)
    with path.open("w") as f:
        json.dump([e.model_dump() for e in new_edges], f, indent=2)

    return True
