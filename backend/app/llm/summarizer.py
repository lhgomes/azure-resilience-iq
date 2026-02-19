import uuid
from typing import Any, Dict, List, Set


def summarize_graph_for_llm(graph: Dict[str, Any]) -> Dict[str, Any]:
    """Build a condensed, LLM-safe view of the graph without mutating the input.

    Keeps canonical ARM ids for traceability, but also provides a compact short_id
    (deterministic UUIDv5); falls back to the canonical id only if short_id is absent.
    Connections use short_id values to minimize token footprint.
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
    id_to_short: Dict[str, str] = {}

    def ensure_short_id(node_id: str, candidate: Any) -> str:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        if not isinstance(node_id, str) or not node_id.strip():
            return "unknown"
        return str(uuid.uuid5(uuid.NAMESPACE_URL, node_id.strip()))

    for n in nodes_raw:
        nd = as_dict(n)
        node_id = nd.get("id") or ""
        meta = nd.get("metadata") or {}
        short_id = nd.get("short_id") or meta.get("short_id")
        short_id = ensure_short_id(node_id, short_id)
        id_to_short[node_id] = short_id

        safe_nodes.append(
            {
                "id": node_id,
                "short_id": short_id,
                "type": nd.get("type") or "unknown",
                "name": nd.get("name") or node_id.split("/")[-1] or "unknown",
                "importance": meta.get("importance"),
                "criticality_override": meta.get("criticality_override"),
                "connections": [],
            }
        )

    connections: Dict[str, Set[str]] = {n["short_id"]: set() for n in safe_nodes if n.get("short_id")}
    safe_edges: List[Dict[str, Any]] = []

    for e in edges_raw:
        ed = as_dict(e)
        source = ed.get("source")
        target = ed.get("target")
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
                        "signal_count": len(ev.get("signals", [])),
                    }
                    break

        source_short = id_to_short.get(source, source)
        target_short = id_to_short.get(target, target)

        edge_data = {
            "source": source_short,
            "target": target_short,
            "relationship": relationship,
            "source_kind": ed.get("source"),
            "status": ed.get("status"),
            "confidence": ed.get("confidence"),
        }

        if multi_source_info:
            edge_data["multi_source_signals"] = multi_source_info

        safe_edges.append(edge_data)

        if source_short in connections:
            connections[source_short].add(target_short)
        if target_short in connections:
            connections[target_short].add(source_short)

    for n in safe_nodes:
        nid = n.get("short_id") or n.get("id")
        if not nid:
            continue
        conn_list = sorted(connections.get(nid) or [])
        n["connections"] = conn_list
        n["connection_count"] = len(conn_list)

    return {"nodes": safe_nodes, "edges": safe_edges}
