from pathlib import Path

from app.llm.models import (
    LLMAnnotations,
    NodeAnnotation,
    NodeAnnotationPayload,
    EdgeSuggestionPayload,
)

from app.storage._json_repo import read_json, write_json
from app.config import get_llm_annotations_path


def _path(subscription_id: str) -> Path:
    return get_llm_annotations_path(subscription_id)


def load_llm_annotations(subscription_id: str) -> LLMAnnotations:
    raw = read_json(_path(subscription_id), default={})
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

    edges = [
        EdgeSuggestionPayload(**item)
        for item in edge_items
        if isinstance(item, dict)
    ]

    return LLMAnnotations(nodes=nodes, edges=edges)


def save_llm_annotations(subscription_id: str, annotations: LLMAnnotations):
    payload = annotations.model_dump()
    write_json(_path(subscription_id), payload)
