from __future__ import annotations

from typing import Any, Dict, Optional

from app.config import get_resources_path, get_workload_path
from app.storage._json_repo import read_json, write_json


def _read_dict(path) -> Dict[str, Any]:
    payload = read_json(path, default={})
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        return {"resources": payload}
    return {}


def _get_flow_value(payload: Dict[str, Any], field_name: str, flow: str) -> Optional[str]:
    values = payload.get(field_name)
    if not isinstance(values, dict):
        return None
    value = values.get(flow)
    if value and str(value).strip():
        return str(value).strip()
    return None


def _set_flow_value(payload: Dict[str, Any], field_name: str, flow: str, value: str) -> None:
    values = payload.get(field_name)
    if not isinstance(values, dict):
        values = {}
        payload[field_name] = values

    normalized_value = str(value).strip()
    if normalized_value:
        values[flow] = normalized_value
    else:
        values.pop(flow, None)


def get_subscription_conversation_id(subscription_id: str, flow: str) -> Optional[str]:
    payload = _read_dict(get_resources_path(subscription_id))
    return _get_flow_value(payload, "conversation_ids", flow)


def set_subscription_conversation_id(subscription_id: str, flow: str, conversation_id: str) -> None:
    path = get_resources_path(subscription_id)
    payload = _read_dict(path)
    _set_flow_value(payload, "conversation_ids", flow, conversation_id)
    write_json(path, payload)


def get_workload_conversation_id(workload_id: str, flow: str) -> Optional[str]:
    payload = _read_dict(get_workload_path(workload_id))
    return _get_flow_value(payload, "conversation_ids", flow)


def set_workload_conversation_id(workload_id: str, flow: str, conversation_id: str) -> None:
    path = get_workload_path(workload_id)
    payload = _read_dict(path)
    _set_flow_value(payload, "conversation_ids", flow, conversation_id)
    write_json(path, payload)


def get_subscription_context_fingerprint(subscription_id: str, flow: str) -> Optional[str]:
    payload = _read_dict(get_resources_path(subscription_id))
    return _get_flow_value(payload, "conversation_context_fingerprints", flow)


def set_subscription_context_fingerprint(subscription_id: str, flow: str, fingerprint: str) -> None:
    path = get_resources_path(subscription_id)
    payload = _read_dict(path)
    _set_flow_value(payload, "conversation_context_fingerprints", flow, fingerprint)
    write_json(path, payload)


def get_workload_context_fingerprint(workload_id: str, flow: str) -> Optional[str]:
    payload = _read_dict(get_workload_path(workload_id))
    return _get_flow_value(payload, "conversation_context_fingerprints", flow)


def set_workload_context_fingerprint(workload_id: str, flow: str, fingerprint: str) -> None:
    path = get_workload_path(workload_id)
    payload = _read_dict(path)
    _set_flow_value(payload, "conversation_context_fingerprints", flow, fingerprint)
    write_json(path, payload)


def is_subscription_context_seeded(subscription_id: str, flow: str) -> bool:
    payload = _read_dict(get_resources_path(subscription_id))
    values = payload.get("conversation_context_seeded_by_flow")
    return bool(values.get(flow)) if isinstance(values, dict) else False


def set_subscription_context_seeded(subscription_id: str, flow: str, seeded: bool) -> None:
    path = get_resources_path(subscription_id)
    payload = _read_dict(path)
    values = payload.get("conversation_context_seeded_by_flow")
    if not isinstance(values, dict):
        values = {}
        payload["conversation_context_seeded_by_flow"] = values
    values[flow] = bool(seeded)
    write_json(path, payload)


def is_workload_context_seeded(workload_id: str, flow: str) -> bool:
    payload = _read_dict(get_workload_path(workload_id))
    values = payload.get("conversation_context_seeded_by_flow")
    return bool(values.get(flow)) if isinstance(values, dict) else False


def set_workload_context_seeded(workload_id: str, flow: str, seeded: bool) -> None:
    path = get_workload_path(workload_id)
    payload = _read_dict(path)
    values = payload.get("conversation_context_seeded_by_flow")
    if not isinstance(values, dict):
        values = {}
        payload["conversation_context_seeded_by_flow"] = values
    values[flow] = bool(seeded)
    write_json(path, payload)
