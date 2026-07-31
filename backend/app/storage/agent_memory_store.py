from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from app.config import get_agent_memory_path, get_workload_agent_memory_path
from app.relationships.utils import short_id
from app.storage._json_repo import read_json, write_json

SCHEMA_VERSION = 2
MAX_HANDOFFS = 6


def build_graph_context_fingerprint(graph: Dict[str, Any]) -> str:
    nodes = []
    for node in graph.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        nodes.append(
            {
                "id": str(node.get("id") or ""),
                "type": str(node.get("type") or ""),
            }
        )

    edges = []
    for edge in graph.get("edges", []) or []:
        if not isinstance(edge, dict):
            continue
        edges.append(
            {
                "source": str(edge.get("source") or ""),
                "target": str(edge.get("target") or ""),
                "relationship": str(edge.get("relationship") or edge.get("type") or ""),
            }
        )

    payload = {
        "nodes": sorted(nodes, key=lambda item: item["id"]),
        "edges": sorted(edges, key=lambda item: (item["source"], item["target"], item["relationship"])),
        "resilience_evaluations": graph.get("resilience_evaluations") or {},
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _memory_path(scope_type: str, scope_id: str):
    if scope_type == "workload":
        return get_workload_agent_memory_path(scope_id)
    return get_agent_memory_path(scope_id)


def _compact_text(value: Any, max_chars: int) -> str:
    return " ".join(str(value or "").split())[:max_chars]


def _compact_list(values: Any, max_items: int, max_chars: int) -> List[str]:
    if not isinstance(values, list):
        return []
    result: List[str] = []
    seen: set[str] = set()
    for value in values:
        compact = _compact_text(value, max_chars)
        normalized = compact.casefold()
        if not compact or normalized in seen:
            continue
        seen.add(normalized)
        result.append(compact)
        if len(result) >= max_items:
            break
    return result


def compact_resource_ids(nodes: List[Dict[str, Any]], resource_ids: List[str]) -> List[str]:
    short_ids_by_canonical_id = {
        str(node.get("id") or "").casefold(): str(
            node.get("short_id")
            or (node.get("metadata") or {}).get("short_id")
            or short_id(str(node.get("id") or ""))
        )
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    }
    return [
        short_ids_by_canonical_id.get(str(resource_id).casefold(), str(resource_id))
        for resource_id in resource_ids
    ]


def load_cross_flow_handoffs(
    scope_type: str,
    scope_id: str,
    current_flow: str,
    context_fingerprint: str,
) -> List[Dict[str, Any]]:
    payload = read_json(_memory_path(scope_type, scope_id), default={})
    if not isinstance(payload, dict):
        return []
    if payload.get("schema_version") != SCHEMA_VERSION:
        return []
    if payload.get("context_fingerprint") != context_fingerprint:
        return []

    handoffs = payload.get("handoffs")
    if not isinstance(handoffs, list):
        return []
    return [
        dict(item)
        for item in handoffs
        if isinstance(item, dict) and item.get("flow") != current_flow
    ][-MAX_HANDOFFS:]


def append_cross_flow_handoff(
    *,
    scope_type: str,
    scope_id: str,
    flow: str,
    context_fingerprint: str,
    user_intent: str,
    answer_summary: str,
    resource_ids: List[str],
    recommendation_ids: List[str],
    clarifying_questions: List[str],
) -> None:
    path = _memory_path(scope_type, scope_id)
    payload = read_json(path, default={})
    if not isinstance(payload, dict):
        payload = {}

    same_context = (
        payload.get("schema_version") == SCHEMA_VERSION
        and payload.get("context_fingerprint") == context_fingerprint
    )
    existing_handoffs = payload.get("handoffs") if same_context else []
    compact_flow = _compact_text(flow, 32)
    handoffs = [
        dict(item)
        for item in existing_handoffs or []
        if isinstance(item, dict) and item.get("flow") != compact_flow
    ]
    handoffs.append(
        {
            "flow": compact_flow,
            "user_intent": _compact_text(user_intent, 500),
            "answer_summary": _compact_text(answer_summary, 1000),
            "resource_ids": _compact_list(resource_ids, 8, 300),
            "recommendation_ids": _compact_list(recommendation_ids, 8, 100),
            "clarifying_questions": _compact_list(clarifying_questions, 4, 300),
        }
    )

    write_json(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "revision": int(payload.get("revision") or 0) + 1 if same_context else 1,
            "context_fingerprint": context_fingerprint,
            "handoffs": handoffs[-MAX_HANDOFFS:],
        },
    )