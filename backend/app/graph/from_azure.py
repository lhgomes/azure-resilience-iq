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
from app.config import COLLECTOR_UNIFIED_EDGES_PATH


def load_unified_edges() -> List[Dict[str, Any]]:
    """Load multi-source unified edges from collector output if available."""
    if not COLLECTOR_UNIFIED_EDGES_PATH.exists():
        return []
    
    try:
        raw = json.loads(COLLECTOR_UNIFIED_EDGES_PATH.read_text())
        return raw if isinstance(raw, list) else []
    except Exception:
        return []

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

    # index resources by id (IDs are already normalized from collector)
    by_id: Dict[str, Dict[str, Any]] = {r.get("id"): r for r in resources if r.get("id")}

    # add nodes from resources
    for rid, r in by_id.items():
        base_name = r.get("name") or rid.split("/")[-1]
        importance = node_importance(r.get("type"))

        gb.add_node(Node(
            id=rid,
            type=map_node_type(r.get("type")),
            name=base_name,
            source="arg",
            metadata={
                "azure_type": r.get("type"),
                "location": r.get("location"),
                "resource_group": r.get("resource_group") or r.get("resourceGroup"),
                "subscription_id": r.get("subscription_id") or r.get("subscriptionId"),
                "tags": r.get("tags") or {},
                "importance": importance,
                "display_name": base_name,
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

    # add edges (dedupe handled by edge IDs)
    for (f, t, rel, src, conf, evidence) in all_edges:
        if not f or not t:
            continue
        gb.add_edge(
            source=norm_id(f),
            target=norm_id(t),
            relationship=rel,
            origin=src,
            confidence=conf,
            evidence=evidence
        )

    snapshot = gb.build(workload_id)

    # Load and merge multi-source unified edges (with signal details)
    unified_edges = load_unified_edges()
    unified_edges_by_key = {}
    unified_edges_by_id = {}
    
    for ue in unified_edges:
        # IDs are already normalized from collector
        source = ue.get('source')
        target = ue.get('target')
        ue_id = ue.get('id')
        
        if not source or not target:
            continue
        
        # Skip if nodes don't exist
        if source not in gb.nodes or target not in gb.nodes:
            continue
        
        key = f"{source}|{target}"
        unified_edges_by_key[key] = ue
        if ue_id:
            unified_edges_by_id[ue_id] = ue
    
    # Enrich existing edges with multi-source signal data
    enriched_edges = []
    processed_keys = set()
    
    for edge in snapshot["edges"]:
        key = f"{edge.source}|{edge.target}"
        processed_keys.add(key)
        
        if key in unified_edges_by_key:
            ue = unified_edges_by_key[key]
            # Merge signal information into evidence
            combined_evidence = list(edge.evidence) if edge.evidence else []
            
            # Add signal details as enrichment
            signal_enrichment = {
                "type": "multi_source_signals",
                "signals": ue.get('signal_details', []),
                "aggregated_confidence": ue.get('confidence'),
                "signal_types": ue.get('signals', [])
            }
            combined_evidence.append(signal_enrichment)
            
            # Update edge confidence to use aggregated confidence from multi-source
            enriched_edge = Edge(
                id=edge.id,
                source=edge.source,
                target=edge.target,
                relationship=edge.relationship,
                confidence=max(edge.confidence, ue.get('confidence', edge.confidence)),
                origin=edge.origin,
                evidence=combined_evidence,
                status=edge.status
            )
            enriched_edges.append(enriched_edge)
        else:
            enriched_edges.append(edge)
    
    # Add unified edges that weren't already extracted by other methods
    for key, ue in unified_edges_by_key.items():
        if key in processed_keys:
            continue
        
        # IDs are already normalized from collector
        source = ue.get('source')
        target = ue.get('target')
        relationship = ue.get('relationship', 'relates_to')
        ue_id = ue.get('id')
        
        # Create new edge from unified edge, using pre-computed ID
        edge = Edge(
            id=ue_id or f"{source}|{target}",
            source=source,
            target=target,
            relationship=relationship,
            confidence=ue.get('confidence', 0.7),
            origin='multi_source',
            evidence=[{
                "type": "multi_source_signals",
                "signals": ue.get('signal_details', []),
                "aggregated_confidence": ue.get('confidence'),
                "signal_types": ue.get('signals', [])
            }],
            status=EdgeStatus.proposed
        )
        enriched_edges.append(edge)

    # merge user-created (manual) edges, mark as accepted and keep deduped
    manual_edges = load_manual_edges(workload_id)
    existing_ids = {edge.id for edge in enriched_edges}

    merged_edges = list(enriched_edges)
    for me in manual_edges:
        if me.id in existing_ids:
            continue

        if norm_id(me.source) not in gb.nodes or norm_id(me.target) not in gb.nodes:
            continue

        merged_edges.append(Edge(
            id=me.id,
            source=norm_id(me.source),
            target=norm_id(me.target),
            relationship=me.relationship,
            confidence=me.confidence,
            origin=me.origin,
            evidence=[],
            status=EdgeStatus.accepted
        ))

    snapshot["edges"] = merged_edges
    return snapshot
