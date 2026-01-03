from typing import Any, Dict, List, Set

from .models import LLMSummary, LLMSafeNode, LLMSafeEdge


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

        safe_edges.append(
            {
                "source": source,
                "target": target,
                "relationship": relationship,
            }
        )

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


def _as_dict(item: Any) -> Dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump()
    if hasattr(item, "dict"):
        # pydantic v1
        return item.dict()
    return getattr(item, "__dict__", {})


def build_llm_safe_summary(snapshot: Dict[str, Any]) -> LLMSummary:
    nodes_raw: List[Any] = snapshot.get("nodes", [])
    edges_raw: List[Any] = snapshot.get("edges", [])

    alias_to_node_id: Dict[str, str] = {}
    safe_nodes: List[LLMSafeNode] = []

    for idx, n in enumerate(nodes_raw):
        nd = _as_dict(n)
        node_id = nd.get("id") or ""
        alias = f"n{idx + 1}"
        alias_to_node_id[alias] = node_id

        metadata = nd.get("metadata") or {}
        safe_nodes.append(
            LLMSafeNode(
                alias=alias,
                type=nd.get("type") or "unknown",
                name=nd.get("name") or alias,
                importance=metadata.get("importance"),
                connections=[],
            )
        )

    node_id_to_alias = {v: k for k, v in alias_to_node_id.items() if v}

    connections = {n.alias: set() for n in safe_nodes}
    safe_edges: List[LLMSafeEdge] = []

    for e in edges_raw:
        ed = _as_dict(e)
        source = ed.get("from_id") or ed.get("source")
        target = ed.get("to_id") or ed.get("target")
        relationship = ed.get("relationship") or "related_to"

        if not source or not target:
            continue

        s_alias = node_id_to_alias.get(source)
        t_alias = node_id_to_alias.get(target)
        if not s_alias or not t_alias:
            continue

        safe_edges.append(
            LLMSafeEdge(
                source=s_alias,
                target=t_alias,
                relationship=relationship,
            )
        )
        connections[s_alias].add(t_alias)
        connections[t_alias].add(s_alias)

    patched_nodes: List[LLMSafeNode] = []
    for n in safe_nodes:
        conn = sorted(connections.get(n.alias) or [])
        patched_nodes.append(n.copy(update={"connections": conn}))

    return LLMSummary(
        nodes=patched_nodes,
        edges=safe_edges,
        # keep alias map for post-processing only; do not share externally
        alias_to_node_id=alias_to_node_id,
    )
