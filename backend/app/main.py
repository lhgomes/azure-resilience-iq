import logging
from pathlib import Path
import json
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

from app.graph.builder import GraphBuilder, edge_id
from app.graph.model import Node
from app.graph.from_azure import build_graph_from_resources
from app.intent.manual_edge import ManualEdge
from app.intent.node_override import NodeOverride

from app.intent.overrides import EdgeOverride, EdgeDecision
from app.storage.json_store import load_snapshot, save_snapshot
from app.storage.manual_edges_store import save_manual_edge, delete_manual_edge
from app.storage.node_overrides_store import (
    save_node_override,
    load_node_overrides,
    delete_node_override,
)
from app.storage.overrides_store import load_overrides, save_override
from app.storage.llm_annotations_store import load_llm_annotations, save_llm_annotations
from app.storage.criticality_overrides_store import (
    load_criticality_overrides,
    get_criticality_override,
    save_criticality_override,
    delete_criticality_override,
)
from app.relationships.utils import norm_id
from app.llm.annotator import annotate_graph, llm_config_enabled

# Load environment variables from .env file (if it exists)
# This allows setting AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT, etc.
# without passing them on the command line every time.
load_dotenv()

LOGGER = logging.getLogger(__name__)

COLLECTOR_RESOURCES = Path("data/collector/resources.json")

app = FastAPI(title="Azure Workload Graph")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateEdgeRequest(BaseModel):
    from_id: str
    to_id: str
    relationship: str
    confidence: float | None = 1.0


class UpdateNodeRequest(BaseModel):
    name: str | None = None
    layer: int | None = None
    shape: str | None = None
    color: str | None = None


class UpdateCriticalityRequest(BaseModel):
    score: int


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

    saved = save_criticality_override(workload_id, node_id_norm, score)
    if not saved:
        raise HTTPException(
            status_code=400,
            detail="Failed to save criticality score"
        )

    return {
        "status": "updated",
        "node_id": node_id_norm,
        "criticality_score": score
    }

@app.delete("/api/workloads/{workload_id}/nodes/{node_id:path}/criticality")
def reset_criticality_score(workload_id: str, node_id: str):
    """Reset criticality score override for a node (reverts to LLM suggestion)."""
    node_id_norm = norm_id(node_id)
    deleted = delete_criticality_override(workload_id, node_id_norm)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Criticality override not found"
        )

    return {
        "status": "deleted",
        "node_id": node_id_norm
    }

@app.patch("/api/workloads/{workload_id}/nodes/{node_id:path}")
def update_node(workload_id: str, node_id: str, payload: UpdateNodeRequest):
    node_id_norm = norm_id(node_id)
    if not any([payload.name, payload.layer is not None, payload.shape, payload.color]):
        raise HTTPException(status_code=400, detail="at least one field is required")

    existing = load_node_overrides(workload_id).get(node_id_norm)

    name = payload.name.strip() if payload.name is not None else (existing.name if existing else None)
    layer = payload.layer if payload.layer is not None else (existing.layer if existing else None)
    shape = payload.shape if payload.shape is not None else (existing.shape if existing else None)
    color = payload.color if payload.color is not None else (existing.color if existing else None)

    override = NodeOverride(
        node_id=node_id_norm,
        name=name,
        layer=layer,
        shape=shape,
        color=color,
    )
    save_node_override(workload_id, override)
    return {
        "status": "updated",
        "node_id": node_id_norm,
        "name": name,
        "layer": payload.layer,
        "shape": payload.shape,
        "color": payload.color,
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
    from_id = norm_id(payload.from_id)
    to_id = norm_id(payload.to_id)

    if not from_id or not to_id:
        raise HTTPException(
            status_code=400,
            detail="from_id and to_id are required"
        )

    if from_id == to_id:
        raise HTTPException(
            status_code=400,
            detail="from_id and to_id must be different"
        )

    if not payload.relationship:
        raise HTTPException(
            status_code=400,
            detail="relationship is required"
        )

    eid = edge_id(from_id, to_id, payload.relationship)

    manual_edge = ManualEdge(
        id=eid,
        from_id=from_id,
        to_id=to_id,
        relationship=payload.relationship,
        confidence=payload.confidence or 1.0,
        status="accepted",
        source="manual",
    )

    save_manual_edge(workload_id, manual_edge)

    return {"edge": manual_edge}

@app.post("/api/workloads/{workload_id}/edges/{edge_id}/accept")
def accept_edge(workload_id: str, edge_id: str):
    override = EdgeOverride(
        edge_id=edge_id,
        decision=EdgeDecision.accepted
    )
    save_override(workload_id, override)
    return {"status": "accepted", "edge_id": edge_id}

@app.post("/api/workloads/{workload_id}/edges/{edge_id}/reject")
def reject_edge(workload_id: str, edge_id: str, reason: str | None = None):
    override = EdgeOverride(
        edge_id=edge_id,
        decision=EdgeDecision.rejected,
        reason=reason
    )
    save_override(workload_id, override)
    return {"status": "rejected", "edge_id": edge_id}

@app.delete("/api/workloads/{workload_id}/edges/{edge_id}")
def remove_manual_edge(workload_id: str, edge_id: str):
    deleted = delete_manual_edge(workload_id, edge_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Manual edge not found"
        )

    return {"status": "deleted", "edge_id": edge_id}

@app.get("/api/workloads/{workload_id}/graph")
def get_graph(workload_id: str, include_llm: bool = False):
    if not COLLECTOR_RESOURCES.exists():
        raise HTTPException(
            status_code=404,
            detail="No collector output found. Run the ARG collector first."
        )

    resources = json.loads(COLLECTOR_RESOURCES.read_text())
    snapshot = build_graph_from_resources(resources, workload_id)

    if not include_llm:
        return snapshot

    # Load pre-computed annotations from disk
    # (These should be generated by running: python -m app.llm.run --workload-id <id>)
    annotations = load_llm_annotations(workload_id)

    payload = {
        **snapshot,
        "llm_annotations": annotations.model_dump(),
    }

    return payload

@app.get("/api/workloads/{workload_id}/reviews")
def review_inbox(workload_id: str):
    snapshot = load_snapshot(workload_id)
    overrides = load_overrides(workload_id)

    pending = [
        e for e in snapshot["edges"]
        if e["id"] not in overrides
    ]

    return {
        "pending": pending,
        "count": len(pending)
    }

