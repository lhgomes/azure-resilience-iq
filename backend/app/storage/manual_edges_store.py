from pathlib import Path
from typing import List

from app.intent.manual_edge import ManualEdge
from app.storage._json_repo import DATA_DIR, read_json, write_json

BASE = DATA_DIR / "manual_edges"


def _path(workload_id: str) -> Path:
    return BASE / f"{workload_id}.json"


def load_manual_edges(workload_id: str) -> List[ManualEdge]:
    raw = read_json(_path(workload_id), default=[])
    if not isinstance(raw, list):
        return []
    return [ManualEdge(**item) for item in raw if isinstance(item, dict)]


def save_manual_edge(workload_id: str, edge: ManualEdge):
    edges = load_manual_edges(workload_id)

    # dedupe by id so the latest write wins
    edges = [e for e in edges if e.id != edge.id]
    edges.append(edge)

    write_json(_path(workload_id), [e.model_dump() for e in edges])


def delete_manual_edge(workload_id: str, edge_id: str) -> bool:
    edges = load_manual_edges(workload_id)
    new_edges = [e for e in edges if e.id != edge_id]

    if len(new_edges) == len(edges):
        return False

    write_json(_path(workload_id), [e.model_dump() for e in new_edges])

    return True
