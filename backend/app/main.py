import logging
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

from app.graph.builder import edge_id as build_edge_id
from app.services.workloads import get_workload_graph, get_review_inbox
from app.intent.manual_edge import ManualEdge
from app.intent.node_override import NodeOverride

from app.intent.overrides import EdgeOverride, EdgeDecision
from app.storage.manual_edges_store import save_manual_edge, delete_manual_edge, load_manual_edges
from app.storage.node_overrides_store import (
    save_node_override,
    load_node_overrides,
    delete_node_override,
)
from app.storage.edge_overrides_store import load_overrides, save_override
from app.storage.groups_store import (
    load_groups,
    save_group,
    delete_group,
    add_node_to_group,
    remove_node_from_group,
    NodeGroup,
)
from app.relationships.utils import norm_id

# Load environment variables from .env file (if it exists)
# This allows setting AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT, etc.
# without passing them on the command line every time.
load_dotenv()

LOGGER = logging.getLogger(__name__)

app = FastAPI(title="Azure Workload Graph")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateEdgeRequest(BaseModel):
    source: str
    target: str
    relationship: str
    confidence: float | None = 1.0


class UpdateNodeRequest(BaseModel):
    name: str | None = None
    layer: int | None = None
    color: str | None = None
    icon: str | None = None
    criticality_score: int | None = None


class UpdateCriticalityRequest(BaseModel):
    score: int


class CreateGroupRequest(BaseModel):
    id: str
    name: str
    nodes: list[str]


class UpdateGroupRequest(BaseModel):
    name: str | None = None
    nodes: list[str] | None = None


class AddNodeToGroupRequest(BaseModel):
    node_id: str


@app.get("/health")
def health():
    return {"status": "ok"}

@app.patch("/api/workloads/{workload_id}/nodes/{node_id:path}/criticality")
def update_criticality_score(workload_id: str, node_id: str, payload: UpdateCriticalityRequest):
    """Update the criticality score (1-10) for a node. Persists as user override."""
    node_id_norm = norm_id(node_id)
    score = payload.score

    if not isinstance(score, int) or score < 1 or score > 10:
        raise HTTPException(
            status_code=400,
            detail="criticality_score must be an integer between 1 and 10"
        )

    existing = load_node_overrides(workload_id).get(node_id_norm)
    
    override = NodeOverride(
        node_id=node_id_norm,
        name=existing.name if existing else None,
        layer=existing.layer if existing else None,
        color=existing.color if existing else None,
        icon=existing.icon if existing else None,
        group_id=existing.group_id if existing else None,
        group_label=existing.group_label if existing else None,
        criticality_score=score,
    )

    save_node_override(workload_id, override)
    
    return {
        "status": "updated",
        "node_id": node_id_norm,
        "criticality_score": score
    }

@app.delete("/api/workloads/{workload_id}/nodes/{node_id:path}/criticality")
def reset_criticality_score(workload_id: str, node_id: str):
    """Reset criticality score override for a node (reverts to LLM suggestion)."""
    node_id_norm = norm_id(node_id)
    existing = load_node_overrides(workload_id).get(node_id_norm)
    
    if not existing or existing.criticality_score is None:
        raise HTTPException(
            status_code=404,
            detail="Criticality override not found"
        )
    
    # Clear just the criticality score, keep other overrides
    override = NodeOverride(
        node_id=node_id_norm,
        name=existing.name,
        layer=existing.layer,
        color=existing.color,
        icon=existing.icon,
        group_id=existing.group_id,
        group_label=existing.group_label,
        criticality_score=None,
    )
    
    # If all fields are None, delete the entire override
    if all(v is None for k, v in override.model_dump().items() if k != 'node_id' and k != 'created_by'):
        delete_node_override(workload_id, node_id_norm)
    else:
        save_node_override(workload_id, override)

    return {
        "status": "deleted",
        "node_id": node_id_norm
    }

@app.patch("/api/workloads/{workload_id}/nodes/{node_id:path}")
def update_node(workload_id: str, node_id: str, payload: UpdateNodeRequest):
    node_id_norm = norm_id(node_id)
    if not payload.model_fields_set:
        raise HTTPException(status_code=400, detail="at least one field is required")

    existing = load_node_overrides(workload_id).get(node_id_norm)

    name = (
        (payload.name.strip() if payload.name is not None else None)
        if "name" in payload.model_fields_set
        else (existing.name if existing else None)
    )
    if name == "":
        name = None

    layer = payload.layer if "layer" in payload.model_fields_set else (existing.layer if existing else None)
    color = payload.color if "color" in payload.model_fields_set else (existing.color if existing else None)
    icon = payload.icon if "icon" in payload.model_fields_set else (existing.icon if existing else None)

    group_id = payload.group_id if "group_id" in payload.model_fields_set else (existing.group_id if existing else None)
    if group_id is not None and group_id.strip() == "":
        group_id = None

    group_label = payload.group_label if "group_label" in payload.model_fields_set else (existing.group_label if existing else None)
    if group_label is not None:
        group_label = group_label.strip()
        if group_label == "":
            group_label = None

    if icon == "":
        icon = None

    # Handle criticality_score
    criticality_score = None
    if "criticality_score" in payload.model_fields_set:
        criticality_score = payload.criticality_score
        # Validate if provided
        if criticality_score is not None and (not isinstance(criticality_score, int) or criticality_score < 1 or criticality_score > 10):
            raise HTTPException(
                status_code=400,
                detail="criticality_score must be an integer between 1 and 10"
            )
    else:
        criticality_score = existing.criticality_score if existing else None

    override = NodeOverride(
        node_id=node_id_norm,
        name=name,
        layer=layer,
        color=color,
        icon=icon,
        group_id=group_id,
        group_label=group_label,
        criticality_score=criticality_score,
    )

    if (
        override.name is None
        and override.layer is None
        and override.color is None
        and override.icon is None
        and override.group_id is None
        and override.group_label is None
        and override.criticality_score is None
    ):
        # Nothing left to override; remove record if it exists.
        deleted = delete_node_override(workload_id, node_id_norm)
        return {
            "status": "deleted" if deleted else "noop",
            "node_id": node_id_norm,
        }

    save_node_override(workload_id, override)
    return {
        "status": "updated",
        "node_id": node_id_norm,
        "name": name,
        "layer": layer,
        "color": color,
        "icon": icon,
        "group_id": group_id,
        "group_label": group_label,
    }


@app.delete("/api/workloads/{workload_id}/nodes/{node_id:path}")
def remove_node_override(workload_id: str, node_id: str):
    node_id_norm = norm_id(node_id)
    deleted = delete_node_override(workload_id, node_id_norm)

    if not deleted:
        raise HTTPException(status_code=404, detail="Node override not found")

    return {"status": "deleted", "node_id": node_id_norm}

@app.post("/api/workloads/{workload_id}/edges")
def create_manual_edge(workload_id: str, payload: CreateEdgeRequest):
    source = norm_id(payload.source)
    target = norm_id(payload.target)

    if not source or not target:
        raise HTTPException(
            status_code=400,
            detail="source and target are required"
        )

    if source == target:
        raise HTTPException(
            status_code=400,
            detail="source and target must be different"
        )

    if not payload.relationship:
        raise HTTPException(
            status_code=400,
            detail="relationship is required"
        )

    eid = build_edge_id(source, target, payload.relationship)

    manual_edge = ManualEdge(
        id=eid,
        source=source,
        target=target,
        relationship=payload.relationship,
        confidence=payload.confidence or 1.0,
        status="accepted",
        origin="manual",
    )

    save_manual_edge(workload_id, manual_edge)

    return {"edge": manual_edge}

@app.post("/api/workloads/{workload_id}/edges/{edge_id:path}/accept")
def accept_edge(workload_id: str, edge_id: str):
    override = EdgeOverride(
        edge_id=edge_id,
        decision=EdgeDecision.accepted
    )
    save_override(workload_id, override)
    return {"status": "accepted", "edge_id": edge_id}

@app.post("/api/workloads/{workload_id}/edges/{edge_id:path}/reject")
def reject_edge(workload_id: str, edge_id: str, reason: str | None = None):
    override = EdgeOverride(
        edge_id=edge_id,
        decision=EdgeDecision.rejected,
        reason=reason
    )
    save_override(workload_id, override)
    return {"status": "rejected", "edge_id": edge_id}

@app.delete("/api/workloads/{workload_id}/edges/{edge_id:path}")
def remove_manual_edge(workload_id: str, edge_id: str):
    deleted = delete_manual_edge(workload_id, edge_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Manual edge not found"
        )

    return {"status": "deleted", "edge_id": edge_id}


@app.post("/api/workloads/{workload_id}/edges/{edge_id:path}/reverse")
def reverse_manual_edge_direction(workload_id: str, edge_id: str):
    """Reverse the direction of a manual edge (swap source and target)."""
    # Load all manual edges
    edges = load_manual_edges(workload_id)
    
    # Find the edge to reverse
    edge_to_reverse = None
    for edge in edges:
        if edge.id == edge_id:
            edge_to_reverse = edge
            break
    
    if not edge_to_reverse:
        raise HTTPException(
            status_code=404,
            detail="Manual edge not found"
        )
    
    # Update the edge with reversed direction (keep the same ID)
    reversed_edge = ManualEdge(
        id=edge_to_reverse.id,  # Keep same ID
        source=edge_to_reverse.target,  # Swap
        target=edge_to_reverse.source,  # Swap
        relationship=edge_to_reverse.relationship,
        confidence=edge_to_reverse.confidence,
        status=edge_to_reverse.status,
        origin=edge_to_reverse.origin,
        created_by=edge_to_reverse.created_by,
    )
    
    # Save the updated edge (this will replace the old one)
    save_manual_edge(workload_id, reversed_edge)
    
    return {"edge": reversed_edge}


# Group endpoints
@app.get("/api/workloads/{workload_id}/groups")
def get_groups(workload_id: str):
    groups = load_groups(workload_id)
    return [g.model_dump() for g in groups]


@app.post("/api/workloads/{workload_id}/groups")
def create_group(workload_id: str, payload: CreateGroupRequest):
    group = NodeGroup(id=payload.id, name=payload.name, nodes=payload.nodes)
    save_group(workload_id, group)
    return group.model_dump()


@app.patch("/api/workloads/{workload_id}/groups/{group_id}")
def update_group(workload_id: str, group_id: str, payload: UpdateGroupRequest):
    from app.storage.groups_store import get_group
    
    group = get_group(workload_id, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    if payload.name is not None:
        group.name = payload.name
    if payload.nodes is not None:
        group.nodes = payload.nodes
    
    save_group(workload_id, group)
    return group.model_dump()


@app.delete("/api/workloads/{workload_id}/groups/{group_id}")
def delete_group_endpoint(workload_id: str, group_id: str):
    deleted = delete_group(workload_id, group_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Group not found")
    return {"status": "deleted", "group_id": group_id}


@app.post("/api/workloads/{workload_id}/groups/{group_id}/nodes")
def add_node_endpoint(workload_id: str, group_id: str, payload: AddNodeToGroupRequest):
    success = add_node_to_group(workload_id, group_id, payload.node_id)
    if not success:
        raise HTTPException(status_code=404, detail="Group not found")
    return {"status": "added", "group_id": group_id, "node_id": payload.node_id}


@app.delete("/api/workloads/{workload_id}/groups/{group_id}/nodes/{node_id:path}")
def remove_node_endpoint(workload_id: str, group_id: str, node_id: str):
    success = remove_node_from_group(workload_id, group_id, node_id)
    if not success:
        raise HTTPException(status_code=404, detail="Group not found")
    return {"status": "removed", "group_id": group_id, "node_id": node_id}


@app.get("/api/workloads/{workload_id}/graph")
def get_graph(workload_id: str, include_llm: bool = False):
    return get_workload_graph(workload_id, include_llm=include_llm)

@app.get("/api/workloads/{workload_id}/reviews")
def review_inbox(workload_id: str):
    return get_review_inbox(workload_id)

