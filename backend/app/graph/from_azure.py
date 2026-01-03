from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from app.graph.builder import GraphBuilder
from app.graph.model import Edge, EdgeStatus, Node
from app.relationships.utils import norm_id, parent_id

from app.relationships.extract_aks import extract_aks_relationships
from app.relationships.extract_networking import extract_networking_relationships
from app.relationships.extract_private_endpoints import extract_private_endpoint_relationships
from app.storage.manual_edges_store import load_manual_edges
from app.storage.node_overrides_store import load_node_overrides

def node_importance(azure_type: str) -> int:
    t = (azure_type or "").lower()
    if "virtualnetworks" in t:
        return 1
    if "managedclusters" in t or "virtualmachines" in t:
        return 1
    if "subnets" in t:
        return 2
    return 3

# Simple type mapping (expand later)
def map_node_type(azure_type: str) -> str:
    """Map Azure resource types to canonical node types used by the frontend for icon/theme selection."""
    t = (azure_type or "").lower()

    # Compute
    if t in {"microsoft.compute/virtualmachines", "microsoft.compute/virtualmachinescalesets"}:
        return "vm"

    # Containers
    if t == "microsoft.containerservice/managedclusters":
        return "aks"

    # Networking
    if t == "microsoft.network/virtualnetworks":
        return "vnet"
    if t == "microsoft.network/subnets":
        return "subnet"
    if t == "microsoft.network/publicipaddresses":
        return "pip"
    if t == "microsoft.network/networksecuritygroups":
        return "nsg"
    if t == "microsoft.network/networkinterfaces":
        return "nic"
    if t == "microsoft.network/privateendpoints":
        return "private_endpoint"
    if t.startswith("microsoft.network/"):
        return "network"

    # Storage
    if t.startswith("microsoft.storage/storageaccounts"):
        return "storage"
    if t == "microsoft.compute/disks":
        return "disk"

    # Databases
    if t.startswith("microsoft.sql/"):
        return "sql"

    # Security
    if t == "microsoft.keyvault/vaults":
        return "keyvault"

    # Azure OpenAI Service
    if t == "microsoft.cognitiveservices/accounts":
        return "aoai"

    # Management + Governance
    if t == "microsoft.devtestlab/schedules":
        return "schd"

    return "resource"

def build_graph_from_resources(resources: List[Dict[str, Any]], workload_id: str) -> dict:
    gb = GraphBuilder()
    node_overrides = load_node_overrides(workload_id)

    # index resources by id
    by_id: Dict[str, Dict[str, Any]] = {}
    for r in resources:
        rid = norm_id(r.get("id"))
        if rid:
            by_id[rid] = r

    # add nodes from resources
    for rid, r in by_id.items():
        override = node_overrides.get(rid)
        display_name = (override.name if override and override.name else None) or (r.get("name") or rid.split("/")[-1])
        importance_value = override.layer if override and override.layer is not None else node_importance(r.get("type"))

        gb.add_node(Node(
            id=rid,
            type=map_node_type(r.get("type")),
            name=display_name,
            source="arg",
            metadata={
                "azure_type": r.get("type"),
                "location": r.get("location"),
                "resource_group": r.get("resource_group") or r.get("resourceGroup"),
                "subscription_id": r.get("subscription_id") or r.get("subscriptionId"),
                "tags": r.get("tags") or {},
                "importance": importance_value,
                "display_name": display_name,
                "override": bool(override),
                "shape_override": override.shape if override and override.shape else None,
                "color_override": override.color if override and override.color else None,
                "layer_override": override.layer if override and override.layer is not None else None,
            }
        ))

    all_edges: List[Tuple[str, str, str, str, float, list]] = []
    synthetic_nodes: Set[str] = set()

    # AKS
    all_edges += extract_aks_relationships(by_id)

    # Networking (subnets, NSG, route tables, NIC)
    net_edges, net_synth = extract_networking_relationships(by_id)
    all_edges += net_edges
    synthetic_nodes |= net_synth

    # Private Endpoints
    pe_edges, pe_synth = extract_private_endpoint_relationships(by_id)
    all_edges += pe_edges
    synthetic_nodes |= pe_synth

    # create synthetic nodes (subnets etc.) if missing
    for sid in synthetic_nodes:
        if sid in gb.nodes:
            continue

        # subnet naming heuristic
        name = sid.split("/")[-1]
        gb.add_node(Node(
            id=sid,
            type="subnet",
            name=name,
            source="heuristic",
            metadata={"synthetic": True, "kind": "subnet"}
        ))

    # add edges (dedupe handled by GraphBuilder.edge_id)
    for (f, t, rel, src, conf, evidence) in all_edges:
        if not f or not t:
            continue
        gb.add_edge(
            from_id=norm_id(f),
            to_id=norm_id(t),
            relationship=rel,
            source=src,
            confidence=conf,
            evidence=evidence
        )

    snapshot = gb.build(workload_id)

    # merge user-created (manual) edges, mark as accepted and keep deduped
    manual_edges = load_manual_edges(workload_id)
    existing_ids = {edge.id for edge in snapshot["edges"]}

    merged_edges = list(snapshot["edges"])
    for me in manual_edges:
        if me.id in existing_ids:
            continue

        if norm_id(me.from_id) not in gb.nodes or norm_id(me.to_id) not in gb.nodes:
            continue

        merged_edges.append(Edge(
            id=me.id,
            from_id=norm_id(me.from_id),
            to_id=norm_id(me.to_id),
            relationship=me.relationship,
            confidence=me.confidence,
            source=me.source,
            evidence=[],
            status=EdgeStatus.accepted
        ))

    snapshot["edges"] = merged_edges
    return snapshot
