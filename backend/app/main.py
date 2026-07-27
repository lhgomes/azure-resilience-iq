import logging
import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

from app.settings import load_settings
from app.routes.resilience import router as resilience_router
from app.routes.unified_recommendations import router as unified_recommendations_router
from app.routes.terraform import router as terraform_router
from app.routes.chat import router as chat_router, chat_router as chat_availability_router
from app.routes.subscription_mapping import router as subscription_mapping_router
from app.graph.builder import edge_id as build_edge_id
from app.services.workloads import get_workload_graph, get_review_inbox
from app.services.subscriptions import list_subscriptions
from app.intent.manual_edge import ManualEdge
from app.intent.workload import CreateWorkloadRequest, UpdateWorkloadRequest
from app.intent.node_override import NodeOverride
import subprocess
import sys
import threading
import json
from datetime import datetime
from pathlib import Path
from app.config import get_subscription_dir

from app.intent.overrides import EdgeOverride, EdgeDecision
from app.storage.manual_edges_store import save_manual_edge, delete_manual_edge, load_manual_edges, replace_edges_for_origin
from app.storage.node_overrides_store import (
    save_node_override,
    load_node_overrides,
    delete_node_override,
)
from app.graph.bridge_edges import create_bridge_edges_for_hidden_node
from app.services.workloads import get_workload_graph
from app.storage.edge_overrides_store import load_overrides, save_override
from app.storage.groups_store import (
    load_groups,
    save_group,
    delete_group,
    add_node_to_group,
    remove_node_from_group,
    get_group,
    NodeGroup,
)
from app.storage.workload_store import (
    list_workloads as list_saved_workloads,
    get_workload as get_saved_workload,
    create_workload as create_saved_workload,
    update_workload as update_saved_workload,
    delete_workload as delete_saved_workload,
)
from app.storage._json_repo import read_json, write_json, path_exists
from app.relationships.utils import norm_id

# Load application configuration from app_config.yaml
app_settings = load_settings()
LOGGER = logging.getLogger(__name__)
LOGGER.info("Application settings loaded successfully")

# CORS origins are env-driven (comma-separated) and default to local dev origins.
# Set CORS_ALLOWED_ORIGINS explicitly for any non-local deployment.
_DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", _DEFAULT_CORS_ORIGINS).split(",")
    if origin.strip()
]

app = FastAPI(title="Azure Resiliency IQ")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routes
app.include_router(resilience_router)
app.include_router(unified_recommendations_router)
app.include_router(terraform_router)
app.include_router(chat_router)
app.include_router(chat_availability_router)
app.include_router(subscription_mapping_router)


class CreateEdgeRequest(BaseModel):
    source: str
    target: str
    relationship: str
    confidence: float | None = 1.0


class SyncBridgeEdgesRequest(BaseModel):
    edges: list[CreateEdgeRequest] = []


class UpdateNodeRequest(BaseModel):
    name: str | None = None
    layer: int | None = None
    color: str | None = None
    icon: str | None = None
    criticality_score: int | None = None
    hidden: bool | None = None


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
    node_ids: list[str]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/subscriptions")
def get_subscriptions():
    """List all available subscriptions by scanning data directory."""
    return list_subscriptions()


@app.patch("/api/subscriptions/{subscription_id}/nodes/{node_id:path}/criticality")
def update_criticality_score(subscription_id: str, node_id: str, payload: UpdateCriticalityRequest):
    """Update the criticality score (1-10) for a node. Persists as user override."""
    node_id_norm = norm_id(node_id)
    score = payload.score

    if not isinstance(score, int) or score < 1 or score > 10:
        raise HTTPException(
            status_code=400,
            detail="criticality_score must be an integer between 1 and 10"
        )

    existing = load_node_overrides(subscription_id).get(node_id_norm)
    
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

    save_node_override(subscription_id, override)
    
    return {
        "status": "updated",
        "node_id": node_id_norm,
        "criticality_score": score
    }

@app.delete("/api/subscriptions/{subscription_id}/nodes/{node_id:path}/criticality")
def reset_criticality_score(subscription_id: str, node_id: str):
    """Reset criticality score override for a node (reverts to LLM suggestion)."""
    node_id_norm = norm_id(node_id)
    existing = load_node_overrides(subscription_id).get(node_id_norm)
    
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
        delete_node_override(subscription_id, node_id_norm)
    else:
        save_node_override(subscription_id, override)

    return {
        "status": "deleted",
        "node_id": node_id_norm
    }

@app.patch("/api/subscriptions/{subscription_id}/nodes/{node_id:path}")
def update_node(subscription_id: str, node_id: str, payload: UpdateNodeRequest):
    node_id_norm = norm_id(node_id)
    LOGGER.info(f"[PATCH] Received payload: {payload.model_dump(exclude_unset=False)}")
    LOGGER.info(f"[PATCH] Fields set: {payload.model_fields_set}")
    if not payload.model_fields_set:
        raise HTTPException(status_code=400, detail="at least one field is required")

    existing = load_node_overrides(subscription_id).get(node_id_norm)

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
    hidden = payload.hidden if "hidden" in payload.model_fields_set else (existing.hidden if existing else None)
    
    LOGGER.info(f"[PATCH] Extracted hidden value: {hidden} (type: {type(hidden).__name__})")

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
        hidden=hidden,
    )

    if (
        override.name is None
        and override.layer is None
        and override.color is None
        and override.icon is None
        and override.group_id is None
        and override.group_label is None
        and override.criticality_score is None
        and override.hidden is None
    ):
        # Nothing left to override; remove record if it exists.
        deleted = delete_node_override(subscription_id, node_id_norm)
        return {
            "status": "deleted" if deleted else "noop",
            "node_id": node_id_norm,
        }

    save_node_override(subscription_id, override)
    
    # If node is being hidden, create bridge edges to maintain dependency chains
    bridge_edges = []
    if hidden is True:
        try:
            LOGGER.info(f"[Hide] Node {node_id_norm} is being hidden, fetching snapshot to create bridges")
            # Import here to avoid circular imports
            from app.services.workloads import build_workload_snapshot
            
            # Get the raw snapshot (not filtered for hidden nodes) so we can see the node being hidden
            raw_snapshot = build_workload_snapshot(subscription_id)
            if raw_snapshot:
                LOGGER.info(f"[Hide] Got snapshot with {len(raw_snapshot.get('nodes', []))} nodes and {len(raw_snapshot.get('edges', []))} edges")
                bridge_edges = create_bridge_edges_for_hidden_node(
                    subscription_id,
                    node_id_norm,
                    raw_snapshot.get("edges", []),
                    raw_snapshot.get("nodes", [])
                )
            else:
                LOGGER.warning(f"[Hide] Failed to get snapshot for {subscription_id}")
        except Exception as e:
            LOGGER.warning(f"[Hide] Failed to create bridge edges for hidden node {node_id_norm}: {e}", exc_info=True)
    
    return {
        "status": "updated",
        "node_id": node_id_norm,
        "name": name,
        "layer": layer,
        "color": color,
        "icon": icon,
        "group_id": group_id,
        "group_label": group_label,
        "bridge_edges": bridge_edges,
    }


@app.delete("/api/subscriptions/{subscription_id}/nodes/{node_id:path}")
def remove_node_override(subscription_id: str, node_id: str):
    node_id_norm = norm_id(node_id)
    deleted = delete_node_override(subscription_id, node_id_norm)

    if not deleted:
        raise HTTPException(status_code=404, detail="Node override not found")

    return {"status": "deleted", "node_id": node_id_norm}

@app.post("/api/subscriptions/{subscription_id}/edges")
def create_manual_edge(subscription_id: str, payload: CreateEdgeRequest):
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

    save_manual_edge(subscription_id, manual_edge)

    return {"edge": manual_edge}


@app.put("/api/subscriptions/{subscription_id}/bridge-edges")
def sync_bridge_edges(subscription_id: str, payload: SyncBridgeEdgesRequest):
    bridge_edges: list[ManualEdge] = []

    for item in payload.edges:
        source = norm_id(item.source)
        target = norm_id(item.target)
        relationship = item.relationship

        if not source or not target:
            raise HTTPException(status_code=400, detail="source and target are required")

        if source == target:
            raise HTTPException(status_code=400, detail="source and target must be different")

        if not relationship:
            raise HTTPException(status_code=400, detail="relationship is required")

        eid = build_edge_id(source, target, relationship)

        bridge_edges.append(
            ManualEdge(
                id=eid,
                source=source,
                target=target,
                relationship=relationship,
                confidence=item.confidence or 1.0,
                status="accepted",
                origin="bridge",
                created_by="system",
            )
        )

    replace_edges_for_origin(subscription_id, "bridge", bridge_edges)

    return {"count": len(bridge_edges)}


@app.delete("/api/subscriptions/{subscription_id}/bridge-edges")
def clear_bridge_edges(subscription_id: str):
    """Clear all bridge edges for a subscription (used when restoring hidden resources)."""
    replace_edges_for_origin(subscription_id, "bridge", [])
    return {"status": "cleared"}

@app.post("/api/subscriptions/{subscription_id}/edges/{edge_id:path}/accept")
def accept_edge(subscription_id: str, edge_id: str):
    override = EdgeOverride(
        edge_id=edge_id,
        decision=EdgeDecision.accepted
    )
    save_override(subscription_id, override)
    return {"status": "accepted", "edge_id": edge_id}

@app.post("/api/subscriptions/{subscription_id}/edges/{edge_id:path}/reject")
def reject_edge(subscription_id: str, edge_id: str, reason: str | None = None):
    override = EdgeOverride(
        edge_id=edge_id,
        decision=EdgeDecision.rejected,
        reason=reason
    )
    save_override(subscription_id, override)
    return {"status": "rejected", "edge_id": edge_id}

@app.delete("/api/subscriptions/{subscription_id}/edges/{edge_id:path}")
def remove_manual_edge(subscription_id: str, edge_id: str):
    deleted = delete_manual_edge(subscription_id, edge_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Manual edge not found"
        )

    return {"status": "deleted", "edge_id": edge_id}


@app.post("/api/subscriptions/{subscription_id}/edges/{edge_id:path}/reverse")
def reverse_manual_edge_direction(subscription_id: str, edge_id: str):
    """Reverse the direction of a manual edge (swap source and target)."""
    # Load all manual edges
    edges = load_manual_edges(subscription_id)
    
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
    save_manual_edge(subscription_id, reversed_edge)
    
    return {"edge": reversed_edge}


# Group endpoints
@app.get("/api/subscriptions/{subscription_id}/groups")
def get_groups(subscription_id: str):
    groups = load_groups(subscription_id)
    return [g.model_dump() for g in groups]


@app.post("/api/subscriptions/{subscription_id}/groups")
def create_group(subscription_id: str, payload: CreateGroupRequest):
    group = NodeGroup(id=payload.id, name=payload.name, nodes=payload.nodes)
    save_group(subscription_id, group)
    return group.model_dump()


@app.patch("/api/subscriptions/{subscription_id}/groups/{group_id}")
def update_group(subscription_id: str, group_id: str, payload: UpdateGroupRequest):
    group = get_group(subscription_id, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    if payload.name is not None:
        group.name = payload.name
    if payload.nodes is not None:
        group.nodes = payload.nodes
    
    save_group(subscription_id, group)
    return group.model_dump()


@app.delete("/api/subscriptions/{subscription_id}/groups/{group_id}")
def delete_group_endpoint(subscription_id: str, group_id: str):
    deleted = delete_group(subscription_id, group_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Group not found")
    return {"status": "deleted", "group_id": group_id}


@app.post("/api/subscriptions/{subscription_id}/groups/{group_id}/nodes")
def add_node_endpoint(subscription_id: str, group_id: str, payload: AddNodeToGroupRequest):
    added_nodes = []
    for node_id in payload.node_ids:
        success = add_node_to_group(subscription_id, group_id, node_id)
        if success:
            added_nodes.append(node_id)
    
    if not added_nodes:
        raise HTTPException(status_code=404, detail="Group not found or no nodes added")
    
    # Return the full updated group
    updated_group = get_group(subscription_id, group_id)
    if not updated_group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    return updated_group.model_dump()


@app.delete("/api/subscriptions/{subscription_id}/groups/{group_id}/nodes/{node_id:path}")
def remove_node_endpoint(subscription_id: str, group_id: str, node_id: str):
    success = remove_node_from_group(subscription_id, group_id, node_id)
    if not success:
        raise HTTPException(status_code=404, detail="Group not found")
    
    # Return the updated group, or null if it was deleted (< 2 members)
    updated_group = get_group(subscription_id, group_id)
    if updated_group:
        return updated_group.model_dump()
    else:
        return {"status": "deleted", "group_id": group_id}


@app.get("/api/subscriptions/{subscription_id}/graph")
def get_graph(subscription_id: str):
    return get_workload_graph(subscription_id)

@app.get("/api/subscriptions/{subscription_id}/reviews")
def review_inbox(subscription_id: str):
    return get_review_inbox(subscription_id)


@app.get("/api/workloads")
def list_workloads():
    """List saved workload views."""
    return [
        {
            "workload_id": w.workload_id,
            "name": w.name,
            "created_at": w.created_at,
            "updated_at": w.updated_at,
        }
        for w in list_saved_workloads()
    ]


@app.get("/api/workloads/{workload_id}")
def get_workload(workload_id: str):
    """Fetch a saved workload view by id."""
    workload = get_saved_workload(workload_id)
    if not workload:
        raise HTTPException(status_code=404, detail="Workload not found")
    return workload.model_dump()


@app.post("/api/workloads")
def create_workload(req: CreateWorkloadRequest):
    """Create a saved workload view."""
    try:
        workload = create_saved_workload(req.name, req.view_state)
        return workload.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.patch("/api/workloads/{workload_id}")
def update_workload(workload_id: str, req: UpdateWorkloadRequest):
    """Update a saved workload view (name and/or view_state)."""
    try:
        updated = update_saved_workload(workload_id, name=req.name, view_state=req.view_state)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    if not updated:
        raise HTTPException(status_code=404, detail="Workload not found")
    return updated.model_dump()


@app.delete("/api/workloads/{workload_id}")
def delete_workload(workload_id: str):
    """Delete a saved workload view."""
    deleted = delete_saved_workload(workload_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Workload not found")
    return {"status": "deleted", "workload_id": workload_id}


@app.post("/api/subscriptions/{subscription_id}/refresh")
def refresh_subscription(subscription_id: str):
    """Start asynchronous LLM annotation refresh for a subscription.

    Spawns `python -m app.llm.run --subscription-id {subscription_id}` in the background
    and writes status updates to data/{subscription_id}/llm_refresh_status.json for polling.
    """
    try:
        # Prepare status file
        status_file = get_subscription_dir(subscription_id) / "llm_refresh_status.json"

        def write_status(data: dict):
            try:
                write_json(status_file, data)
            except Exception:
                pass

        # If an existing job is running, return current status
        if path_exists(status_file):
            try:
                current = read_json(status_file, default={})
                if current.get("status") == "running":
                    return JSONResponse(
                        status_code=202,
                        content={"status": "running"},
                        headers={
                            "Location": f"/api/subscriptions/{subscription_id}/refresh/status"
                        },
                    )
            except Exception:
                pass

        # Start background process
        cmd = [sys.executable, "-m", "app.llm.run", "--subscription-id", subscription_id]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # Write initial status
        write_status({
            "status": "running",
            "pid": proc.pid,
            "started_at": datetime.utcnow().isoformat() + "Z",
        })

        def monitor():
            try:
                stdout, stderr = proc.communicate()
                rc = proc.returncode
                write_status({
                    "status": "completed" if rc == 0 else "failed",
                    "pid": proc.pid,
                    "started_at": datetime.utcnow().isoformat() + "Z",
                    "finished_at": datetime.utcnow().isoformat() + "Z",
                    "returncode": rc,
                    "stdout_tail": stdout[-1000:] if isinstance(stdout, str) else None,
                    "stderr_tail": stderr[-1000:] if isinstance(stderr, str) else None,
                })
            except Exception as e:
                write_status({
                    "status": "failed",
                    "error": str(e),
                    "finished_at": datetime.utcnow().isoformat() + "Z",
                })

        threading.Thread(target=monitor, daemon=True).start()
        return JSONResponse(
            status_code=202,
            content={"status": "running", "pid": proc.pid},
            headers={
                "Location": f"/api/subscriptions/{subscription_id}/refresh/status"
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/subscriptions/{subscription_id}/refresh/status")
def refresh_status(subscription_id: str):
    """Return the current LLM refresh status for polling."""
    try:
        status_file = get_subscription_dir(subscription_id) / "llm_refresh_status.json"
        if not path_exists(status_file):
            return JSONResponse(status_code=200, content={"status": "idle"})
        payload = read_json(status_file, default={})
        if payload.get("status") == "running":
            return Response(status_code=304)
        return JSONResponse(status_code=200, content=payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
