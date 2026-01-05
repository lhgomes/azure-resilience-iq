from __future__ import annotations

import json

from fastapi import HTTPException

from app.graph.from_azure import build_graph_from_resources
from app.config import COLLECTOR_RESOURCES_PATH
from app.storage.llm_annotations_store import load_llm_annotations
from app.storage.edge_overrides_store import load_overrides
from app.storage.node_overrides_store import load_node_overrides


def _load_collector_resources() -> list[dict]:
    if not COLLECTOR_RESOURCES_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="No collector output found. Run the ARG collector first.",
        )

    raw = json.loads(COLLECTOR_RESOURCES_PATH.read_text())
    if not isinstance(raw, list):
        raise HTTPException(status_code=500, detail="Collector output is invalid")

    return raw


def build_workload_snapshot(workload_id: str) -> dict:
    resources = _load_collector_resources()
    return build_graph_from_resources(resources, workload_id)
def get_workload_graph(workload_id: str, *, include_llm: bool = False) -> dict:
    """
    Get the complete workload graph with all data sources.
    
    Always returns raw data, LLM annotations, node overrides, edge overrides, and groups.
    The include_llm parameter is deprecated but kept for backwards compatibility.
    """
    from app.storage.groups_store import load_groups
    
    snapshot = build_workload_snapshot(workload_id)
    annotations = load_llm_annotations(workload_id)
    node_overrides = load_node_overrides(workload_id)
    edge_overrides = load_overrides(workload_id)
    groups = load_groups(workload_id)

    return {
        **snapshot,
        "llm_annotations": annotations.model_dump(),
        "node_overrides": {
            node_id: override.model_dump() 
            for node_id, override in node_overrides.items()
        },
        "edge_overrides": edge_overrides,
        "groups": [g.model_dump() for g in groups],
    }


def get_review_inbox(workload_id: str) -> dict:
    snapshot = build_workload_snapshot(workload_id)
    overrides = load_overrides(workload_id)

    pending: list[dict] = []
    for edge in snapshot.get("edges") or []:
        edge_id = edge.get("id") if isinstance(edge, dict) else getattr(edge, "id", None)
        if not edge_id or edge_id in overrides:
            continue
        pending.append(edge if isinstance(edge, dict) else edge.model_dump())

    return {"pending": pending, "count": len(pending)}
