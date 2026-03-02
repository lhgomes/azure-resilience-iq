from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from app.graph.builder import GraphBuilder
from app.graph.model import Edge, EdgeStatus, Node
from app.relationships.utils import norm_id, parent_id

from app.relationships.extract_aks import extract_aks_relationships
from app.storage.manual_edges_store import load_manual_edges
from app.config import get_edges_path
from app.graph.bridge_edges import create_bridge_edges_for_non_monitored
from app.storage._json_repo import read_json, path_exists


def load_unified_edges(subscription_id: str) -> List[Dict[str, Any]]:
    """Load multi-source unified edges from collector output if available."""
    edges_path = get_edges_path(subscription_id)
    if not path_exists(edges_path):
        return []

    try:
        raw = read_json(edges_path, default=[])
        # Handle new format with subscription metadata
        if isinstance(raw, dict) and "edges" in raw:
            return raw["edges"]
        # Fallback for direct list format
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
        zones_value = r.get("zones")
        if isinstance(zones_value, list):
            zones_value = [str(z).strip() for z in zones_value if str(z).strip()]
            zones_value = zones_value if zones_value else None
        elif zones_value is not None:
            zones_value = [str(zones_value).strip()] if str(zones_value).strip() else None
        zone_value = (
            r.get("zone")
            or r.get("availability_zone")
            or r.get("availabilityZone")
            or (zones_value[0] if isinstance(zones_value, list) and zones_value else None)
        )

        gb.add_node(Node(
            id=rid,
            type=map_node_type(r.get("type")),
            name=base_name,
            source="arg",
            metadata={
                "azure_type": r.get("type"),
                "location": r.get("location"),
                "zone": zone_value,
                "availability_zone": zone_value,
                "zones": zones_value,
                "resource_group": r.get("resource_group") or r.get("resourceGroup"),
                "subscription_id": r.get("subscription_id") or r.get("subscriptionId"),
                "tags": r.get("tags") or {},
                "importance": importance,
                "display_name": base_name,
                "virtual": bool(r.get("virtual", False)),
                "monitored": r.get("monitored", True),  # Preserve monitored flag from resource
            }
        ))

    all_edges: List[Tuple[str, str, str, str, float, list]] = []
    synthetic_nodes: Set[str] = set()

    # Use unified edges from collector if available (primary path)
    unified_edges = load_unified_edges(workload_id)
    if unified_edges:
        # Convert unified edges to legacy tuple format for compatibility
        for edge_data in unified_edges:
            if isinstance(edge_data, dict):
                all_edges.append((
                    edge_data.get('source', ''),
                    edge_data.get('target', ''),
                    edge_data.get('relationship', ''),
                    edge_data.get('origin', 'unknown'),
                    float(edge_data.get('confidence', 0.5)),
                    edge_data.get('evidence', [])
                ))
    else:
        # Fallback: Extract edges using AKS extractor (only non-redundant extractor)
        # Networking, compute, and private endpoint extraction now handled by
        # configuration-driven reference definitions in the collector
        all_edges += extract_aks_relationships(by_id)

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

    # Filter out synthetic subnets - they're not monitored resources
    # Keep track of them so we can create bridge edges before removing them
    synthetic_subnet_ids = {sid for sid in synthetic_nodes if sid in gb.nodes}
    
    # Create bridge edges for synthetic subnets before removing them
    if synthetic_subnet_ids:
        bridge_edges_for_subnets = create_bridge_edges_for_non_monitored(snapshot["edges"], 
                                                                          set(rid for rid in by_id.keys()) - synthetic_subnet_ids)
        # Add bridge edges to the edge list
        for be in bridge_edges_for_subnets:
            be_obj = Edge(
                id=be["id"],
                source=be["source"],
                target=be["target"],
                relationship=be["relationship"],
                confidence=be["confidence"],
                origin=be["origin"],
                evidence=[],
                status=EdgeStatus.proposed
            )
            snapshot["edges"].append(be_obj)
        
        # Remove synthetic subnet nodes and their direct edges
        snapshot["nodes"] = [n for n in snapshot["nodes"] 
                             if not (isinstance(n, Node) and n.id in synthetic_subnet_ids) 
                             and not (isinstance(n, dict) and n.get("id") in synthetic_subnet_ids)
                             and not (hasattr(n, "id") and getattr(n, "id") in synthetic_subnet_ids)]
        
        snapshot["edges"] = [e for e in snapshot["edges"] 
                            if not ((isinstance(e, Edge) and (e.source in synthetic_subnet_ids or e.target in synthetic_subnet_ids))
                                   or (isinstance(e, dict) and (e.get("source") in synthetic_subnet_ids or e.get("target") in synthetic_subnet_ids)))]

    # Load and merge multi-source unified edges (with signal details)
    unified_edges = load_unified_edges(workload_id)
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

        # Manual edges can be cross-subscription, so don't filter based on node existence.
        # The frontend will handle filtering edges whose endpoints aren't visible.
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

    # Separate monitored and non-monitored nodes
    monitored_node_ids = set()
    for n in snapshot["nodes"]:
        if isinstance(n, Node):
            is_monitored = n.metadata.get("monitored", True)
        elif isinstance(n, dict):
            is_monitored = n.get("metadata", {}).get("monitored", True)
        else:
            # Pydantic model or other object
            meta = getattr(n, "metadata", {})
            is_monitored = meta.get("monitored", True) if isinstance(meta, dict) else True
        
        if is_monitored:
            node_id = n.id if isinstance(n, Node) else n.get("id") if isinstance(n, dict) else getattr(n, "id", None)
            if node_id:
                monitored_node_ids.add(node_id)
    
    # Create bridge edges that connect monitored resources through non-monitored intermediates
    bridge_edges = create_bridge_edges_for_non_monitored(merged_edges, monitored_node_ids)
    
    # Convert bridge edges back to Edge objects and add to merged edges
    for be in bridge_edges:
        be_obj = Edge(
            id=be["id"],
            source=be["source"],
            target=be["target"],
            relationship=be["relationship"],
            confidence=be["confidence"],
            origin=be["origin"],
            evidence=[],
            status=EdgeStatus.accepted
        )
        # Avoid duplicates by checking if this edge (source, target) already exists
        if not any(e.source == be_obj.source and e.target == be_obj.target for e in merged_edges):
            merged_edges.append(be_obj)

    snapshot["edges"] = merged_edges
    
    # Filter snapshot to only include monitored nodes and their edges
    filtered_nodes = []
    for n in snapshot["nodes"]:
        if isinstance(n, Node):
            node_id = n.id
        elif isinstance(n, dict):
            node_id = n.get("id")
        else:
            node_id = getattr(n, "id", None)
        
        if node_id in monitored_node_ids:
            filtered_nodes.append(n)
    
    filtered_edges = []
    for e in snapshot["edges"]:
        if isinstance(e, Edge):
            source, target = e.source, e.target
            origin = e.origin
        elif isinstance(e, dict):
            source, target = e.get("source"), e.get("target")
            origin = e.get("origin")
        else:
            source, target = getattr(e, "source", None), getattr(e, "target", None)
            origin = getattr(e, "origin", None)
        
        # Manual edges can be cross-subscription, so allow them even if target is not monitored
        # This preserves cross-subscription dependencies defined by users
        if origin == "manual":
            filtered_edges.append(e)
        elif source in monitored_node_ids and target in monitored_node_ids:
            filtered_edges.append(e)
    
    snapshot["nodes"] = filtered_nodes
    snapshot["edges"] = filtered_edges
    
    return snapshot
