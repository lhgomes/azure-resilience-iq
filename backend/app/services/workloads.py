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

    # Merge LLM annotations into node metadata
    # Priority: node_overrides > llm_annotations > defaults
    nodes = snapshot.get("nodes") or []
    
    # Convert nodes to dicts if they're Pydantic models
    nodes_list = []
    for n in nodes:
        if isinstance(n, dict):
            nodes_list.append(n)
        else:
            nodes_list.append(n.model_dump() if hasattr(n, 'model_dump') else n.__dict__)
    
    nodes_by_id = {n.get("id"): n for n in nodes_list if n.get("id")}
    
    for llm_node in annotations.nodes:
        node_id = llm_node.node_id
        if node_id not in nodes_by_id:
            continue
        
        node = nodes_by_id[node_id]
        if "metadata" not in node:
            node["metadata"] = {}
        
        node_ann = llm_node.annotations
        
        # Merge into metadata, respecting priority order
        if node_ann.display_name and node_id not in node_overrides:
            node["metadata"]["display_name"] = node_ann.display_name
        
        if node_ann.azure_service_category:
            node["metadata"]["azure_service_category"] = node_ann.azure_service_category
        
        if node_ann.azure_service_name:
            node["metadata"]["azure_service_name"] = node_ann.azure_service_name
        
        if node_ann.layer is not None:
            node["metadata"]["layer"] = node_ann.layer
        
        if node_ann.priority:
            node["metadata"]["priority"] = node_ann.priority
        
        if node_ann.criticality_score is not None:
            node["metadata"]["criticality_score"] = node_ann.criticality_score
        
        if node_ann.hide_by_default is not None:
            node["metadata"]["hide_by_default"] = node_ann.hide_by_default
        
        if node_ann.confidence is not None:
            node["metadata"]["llm_confidence"] = node_ann.confidence
        
        if node_ann.reason:
            node["metadata"]["llm_reason"] = node_ann.reason

    return {
        **snapshot,
        "nodes": list(nodes_by_id.values()),
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
