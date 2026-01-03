import json
from pathlib import Path

from app.llm.models import (
    LLMAnnotations,
    NodeAnnotation,
    NodeAnnotationPayload,
    EdgeSuggestionPayload,
)

BASE = Path("data/llm_annotations")
BASE.mkdir(parents=True, exist_ok=True)


def _path(workload_id: str) -> Path:
    return BASE / f"{workload_id}.json"


def load_llm_annotations(workload_id: str) -> LLMAnnotations:
    path = _path(workload_id)
    if not path.exists():
        return LLMAnnotations(nodes=[], edges=[])

    raw = json.loads(path.read_text())
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
    path = _path(workload_id)
    payload = annotations.model_dump()
    with path.open("w") as f:
        json.dump(payload, f, indent=2)
