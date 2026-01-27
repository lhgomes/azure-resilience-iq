from pathlib import Path
from typing import List

from app.intent.manual_edge import ManualEdge
from app.storage._json_repo import read_json, write_json
from app.config import get_manual_edges_path


def _path(subscription_id: str) -> Path:
    return get_manual_edges_path(subscription_id)


def load_manual_edges(subscription_id: str) -> List[ManualEdge]:
    raw = read_json(_path(subscription_id), default=[])
    if not isinstance(raw, list):
        return []
    return [ManualEdge(**item) for item in raw if isinstance(item, dict)]


def save_manual_edge(subscription_id: str, edge: ManualEdge):
    edges = load_manual_edges(subscription_id)

    # dedupe by id so the latest write wins
    edges = [e for e in edges if e.id != edge.id]
    edges.append(edge)

    write_json(_path(subscription_id), [e.model_dump() for e in edges])


def delete_manual_edge(subscription_id: str, edge_id: str) -> bool:
    edges = load_manual_edges(subscription_id)
    new_edges = [e for e in edges if e.id != edge_id]

    if len(new_edges) == len(edges):
        return False

    write_json(_path(subscription_id), [e.model_dump() for e in new_edges])

    return True


def replace_edges_for_origin(subscription_id: str, origin: str, new_edges: list[ManualEdge]):
    edges = load_manual_edges(subscription_id)
    preserved = [e for e in edges if e.origin != origin]

    dedup: dict[str, ManualEdge] = {e.id: e for e in preserved}
    for edge in new_edges:
        dedup[edge.id] = edge

    write_json(_path(subscription_id), [e.model_dump() for e in dedup.values()])
