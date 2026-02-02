from __future__ import annotations
import uuid
from typing import Any, Dict, Iterable, Optional

def norm_id(resource_id: str) -> str:
    return (resource_id or "").strip().lower()


def is_azure_resource_id(value: Any) -> bool:
    """
    Check if a value is an Azure resource ID.
    Azure resource IDs follow the pattern: /subscriptions/{guid}/...
    """
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    return normalized.startswith('/subscriptions/')


def short_id(resource_id: str) -> str:
    """Deterministic compact ID derived from the canonical ARM ID."""
    rid = norm_id(resource_id)
    if not rid:
        return ""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, rid))

def safe_get(d: Dict[str, Any], path: str) -> Any:
    """
    safe_get(obj, "a.b.c") -> obj["a"]["b"]["c"] or None
    """
    cur: Any = d
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur

def parent_id(child_id: str, marker: str) -> Optional[str]:
    """
    parent_id("<...>/virtualNetworks/vnet1/subnets/s1", "/subnets/")
    -> "<...>/virtualNetworks/vnet1"
    """
    cid = norm_id(child_id)
    m = marker.lower()
    if m not in cid:
        return None
    return cid.split(m)[0]

def iter_ids(value: Any) -> Iterable[str]:
    """
    Normalize different shapes into iterable of IDs:
    - string -> [string]
    - dict with "id" -> [id]
    - list -> flatten
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        if "id" in value and isinstance(value["id"], str):
            return [value["id"]]
        return []
    if isinstance(value, list):
        out = []
        for x in value:
            out.extend(list(iter_ids(x)))
        return out
    return []
