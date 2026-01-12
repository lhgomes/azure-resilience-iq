from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException

from app.graph.from_azure import build_graph_from_resources
from app.config import get_resources_path
from app.storage.llm_annotations_store import load_llm_annotations
from app.storage.edge_overrides_store import load_overrides
from app.storage.node_overrides_store import load_node_overrides
from app.storage.resilience_evaluations_store import load_resilience_evaluations

# Load icon mappings at module level from backend data directory
from app.config import DATA_DIR

ICON_MAPPINGS_PATH = DATA_DIR / "iconMappings.json"
ICON_MAPPINGS = {}
if ICON_MAPPINGS_PATH.exists():
    with open(ICON_MAPPINGS_PATH, 'r') as f:
        ICON_MAPPINGS = json.load(f)
        print(f"✓ Loaded {len(ICON_MAPPINGS)} icon mappings from {ICON_MAPPINGS_PATH}")
else:
    print(f"✗ Icon mappings file not found at {ICON_MAPPINGS_PATH}")


def get_icon_for_resource_type(resource_type: str) -> str | None:
    """Get icon path for a resource type using LLM-generated mappings."""
    if not resource_type:
        return None
    
    normalized_type = resource_type.lower().strip()
    icon_path = ICON_MAPPINGS.get(normalized_type)
    
    if icon_path:
        return f"/Icons/{icon_path}"
    
    return None


def _load_collector_resources(subscription_id: str) -> list[dict]:
    resources_path = get_resources_path(subscription_id)
    if not resources_path.exists():
        raise HTTPException(
            status_code=404,
            detail="No collector output found. Run the ARG collector first.",
        )

    raw = json.loads(resources_path.read_text())
    
    # Handle new format with subscription metadata
    if isinstance(raw, dict) and "resources" in raw:
        return raw["resources"]
    
    # Fallback for direct list format
    if not isinstance(raw, list):
        raise HTTPException(status_code=500, detail="Collector output is invalid")

    return raw


def build_workload_snapshot(subscription_id: str) -> dict:
    resources = _load_collector_resources(subscription_id)
    return build_graph_from_resources(resources, subscription_id)
def get_workload_graph(subscription_id: str) -> dict:
    """
    Get the complete workload graph with all data sources.
    
    Always returns raw data, LLM annotations, node overrides, edge overrides, and groups.
    """
    from app.storage.groups_store import load_groups
    
    snapshot = build_workload_snapshot(subscription_id)
    annotations = load_llm_annotations(subscription_id)
    node_overrides = load_node_overrides(subscription_id)
    edge_overrides = load_overrides(subscription_id)
    groups = load_groups(subscription_id)
    
    # Load resilience evaluations if available
    resilience_data = {}
    try:
        resilience_results = load_resilience_evaluations(subscription_id)
        resilience_evals = resilience_results.get("evaluations", {})
        resilience_data = {
            resource_id: {
                "total_checks": eval_data.get("total_checks", 0),
                "passed_checks": eval_data.get("passed_checks", 0),
                "failed_checks": eval_data.get("failed_checks", 0),
                "pass_percentage": round((eval_data.get("passed_checks", 0) / eval_data.get("total_checks", 1) * 100), 1) if eval_data.get("total_checks", 0) > 0 else 0
            }
            for resource_id, eval_data in resilience_evals.items()
        }
    except Exception:
        # Resilience data is optional
        pass

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
        
        # Compute icon from Azure resource type using LLM-generated mappings
        # The actual resource type is in metadata.azure_type (e.g., "microsoft.app/containerapps")
        azure_type = node.get("metadata", {}).get("azure_type")
        if azure_type and node_id not in node_overrides:
            icon_path = get_icon_for_resource_type(azure_type)
            if icon_path:
                node["metadata"]["icon"] = icon_path
        
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

    # Attach resilience data to each node
    for node_id, node in nodes_by_id.items():
        if node_id in resilience_data:
            if "metadata" not in node:
                node["metadata"] = {}
            node["metadata"]["resilience"] = resilience_data[node_id]

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
        "resilience_evaluations": resilience_results if resilience_results else {"evaluations": {}},
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
