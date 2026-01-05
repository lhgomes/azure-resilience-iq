from typing import Any, Dict, List, Set


def summarize_graph_for_llm(graph: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a condensed, LLM-safe view of the graph without mutating the input.

    Input example (abridged):
    {
      "nodes": [
        {"id": "/subs/123/rg/demo/aks", "type": "aks", "name": "demo-aks", "metadata": {"importance": 1}},
        {"id": "/subs/123/rg/demo/vnet", "type": "vnet", "name": "demo-vnet"}
      ],
      "edges": [
        {"from_id": "/subs/123/rg/demo/aks", "to_id": "/subs/123/rg/demo/vnet", "relationship": "connected_to"}
      ]
    }

    Output example (sanitized):
    {
      "nodes": [
        {"id": "/subs/123/rg/demo/aks", "type": "aks", "name": "demo-aks", "importance": 1, "connections": ["/subs/123/rg/demo/vnet"]},
        {"id": "/subs/123/rg/demo/vnet", "type": "vnet", "name": "demo-vnet", "connections": ["/subs/123/rg/demo/aks"]}
      ],
      "edges": [
        {"source": "/subs/123/rg/demo/aks", "target": "/subs/123/rg/demo/vnet", "relationship": "connected_to"}
      ]
    }

    Note: subscription IDs and ARM paths remain on the node id for traceability,
    but noisy metadata (tags, credentials, properties) are omitted.
    """

    nodes_raw: List[Any] = graph.get("nodes", []) or []
    edges_raw: List[Any] = graph.get("edges", []) or []

    def as_dict(item: Any) -> Dict[str, Any]:
        if isinstance(item, dict):
            return item
        if hasattr(item, "model_dump"):
            return item.model_dump()
        if hasattr(item, "dict"):
            return item.dict()
        return getattr(item, "__dict__", {})

    safe_nodes: List[Dict[str, Any]] = []
    node_ids: Set[str] = set()

    for n in nodes_raw:
        nd = as_dict(n)
        node_id = nd.get("id") or ""
        node_ids.add(node_id)
        meta = nd.get("metadata") or {}

        safe_nodes.append(
            {
                "id": node_id,
                "type": nd.get("type") or "unknown",
                "name": nd.get("name") or node_id.split("/")[-1] or "unknown",
                "importance": meta.get("importance"),
                # Preserve user criticality overrides so the LLM can honor them.
                "criticality_override": meta.get("criticality_override"),
                "connections": [],
            }
        )

    connections: Dict[str, Set[str]] = {n["id"]: set() for n in safe_nodes if n.get("id")}
    safe_edges: List[Dict[str, Any]] = []

    for e in edges_raw:
        ed = as_dict(e)
        source = ed.get("from_id") or ed.get("source")
        target = ed.get("to_id") or ed.get("target")
        relationship = ed.get("relationship") or "related_to"

        if not source or not target:
            continue

        # Extract multi-source signal information if available
        evidence = ed.get("evidence") or []
        multi_source_info = None
        if evidence and isinstance(evidence, list):
            for ev in evidence:
                if isinstance(ev, dict) and ev.get("type") == "multi_source_signals":
                    multi_source_info = {
                        "signals": ev.get("signal_types", []),
                        "aggregated_confidence": ev.get("aggregated_confidence"),
                        "signal_count": len(ev.get("signals", []))
                    }
                    break

        edge_data = {
            "source": source,
            "target": target,
            "relationship": relationship,
            # Preserve provenance and moderation state so the LLM knows which
            # relationships were user-authored or previously accepted.
            "source_kind": ed.get("source"),
            "status": ed.get("status"),
            "confidence": ed.get("confidence"),
        }
        
        # Add multi-source signal context if available
        if multi_source_info:
            edge_data["multi_source_signals"] = multi_source_info
        
        safe_edges.append(edge_data)

        if source in connections:
            connections[source].add(target)
        if target in connections:
            connections[target].add(source)

    for n in safe_nodes:
        nid = n.get("id")
        if not nid:
            continue
        n["connections"] = sorted(connections.get(nid) or [])

    return {"nodes": safe_nodes, "edges": safe_edges}
