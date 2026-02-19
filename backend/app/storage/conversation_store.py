from __future__ import annotations

from typing import Any, Dict, Optional

from app.config import get_resources_path, get_workload_path
from app.storage._json_repo import read_json, write_json


def _read_dict(path) -> Dict[str, Any]:
    payload = read_json(path, default={})
    if isinstance(payload, dict):
        return payload
    return {}


def get_subscription_conversation_id(subscription_id: str) -> Optional[str]:
    payload = _read_dict(get_resources_path(subscription_id))
    conversation_id = payload.get("conversation_id")
    if conversation_id and str(conversation_id).strip():
        return str(conversation_id).strip()
    return None


def set_subscription_conversation_id(subscription_id: str, conversation_id: str) -> None:
    path = get_resources_path(subscription_id)
    payload = _read_dict(path)
    payload["conversation_id"] = str(conversation_id).strip()
    write_json(path, payload)


def get_workload_conversation_id(workload_id: str) -> Optional[str]:
    payload = _read_dict(get_workload_path(workload_id))
    conversation_id = payload.get("conversation_id")
    if conversation_id and str(conversation_id).strip():
        return str(conversation_id).strip()
    return None


def set_workload_conversation_id(workload_id: str, conversation_id: str) -> None:
    path = get_workload_path(workload_id)
    payload = _read_dict(path)
    payload["conversation_id"] = str(conversation_id).strip()
    write_json(path, payload)


def is_subscription_context_seeded(subscription_id: str) -> bool:
    payload = _read_dict(get_resources_path(subscription_id))
    return bool(payload.get("conversation_context_seeded"))


def set_subscription_context_seeded(subscription_id: str, seeded: bool) -> None:
    path = get_resources_path(subscription_id)
    payload = _read_dict(path)
    payload["conversation_context_seeded"] = bool(seeded)
    write_json(path, payload)


def is_workload_context_seeded(workload_id: str) -> bool:
    payload = _read_dict(get_workload_path(workload_id))
    return bool(payload.get("conversation_context_seeded"))


def set_workload_context_seeded(workload_id: str, seeded: bool) -> None:
    path = get_workload_path(workload_id)
    payload = _read_dict(path)
    payload["conversation_context_seeded"] = bool(seeded)
    write_json(path, payload)
