from pathlib import Path

from app.llm.models import (
    LLMAnnotations,
    NodeAnnotation,
    NodeAnnotationPayload,
    EdgeSuggestionPayload,
)

from app.storage._json_repo import DATA_DIR, read_json, write_json

BASE = DATA_DIR / "llm_annotations"


def _path(workload_id: str) -> Path:
    return BASE / f"{workload_id}.json"


def load_llm_annotations(workload_id: str) -> LLMAnnotations:
    raw = read_json(_path(workload_id), default={})
    if not isinstance(raw, dict):
        return LLMAnnotations(nodes=[], edges=[])
    node_items = raw.get("nodes") or []
    edge_items = raw.get("edges") or []

    nodes = [
        NodeAnnotation(
            node_id=item.get("node_id"),
            annotations=NodeAnnotationPayload(**(item.get("annotations") or {})),
        )
        for item in node_items
        if item.get("node_id")
    ]

    edges = [EdgeSuggestionPayload(**item) for item in edge_items if isinstance(item, dict)]

    return LLMAnnotations(nodes=nodes, edges=edges)


def save_llm_annotations(workload_id: str, annotations: LLMAnnotations):
    payload = annotations.model_dump()
    write_json(_path(workload_id), payload)
