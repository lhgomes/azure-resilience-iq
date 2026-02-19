from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from app.config import get_workload_path, get_workloads_dir
from app.intent.workload import Workload, WorkloadViewState
from app.storage._json_repo import read_json, write_json


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _normalize_name(name: str) -> str:
    return (name or "").strip().lower()


def _load_workload(path: Path) -> Optional[Workload]:
    raw = read_json(path, default=None)
    if not isinstance(raw, dict):
        return None

    workload_id = raw.get("workload_id") or path.stem
    name = raw.get("name")
    view_state_raw = raw.get("view_state") or {}
    created_at = raw.get("created_at") or _now()
    updated_at = raw.get("updated_at") or created_at
    conversation_id = raw.get("conversation_id")

    if not isinstance(name, str) or not name.strip():
        return None

    try:
        view_state = WorkloadViewState(**view_state_raw)
    except Exception:
        view_state = WorkloadViewState()

    return Workload(
        workload_id=str(workload_id),
        name=name.strip(),
        view_state=view_state,
        created_at=str(created_at),
        updated_at=str(updated_at),
        conversation_id=str(conversation_id).strip() if conversation_id else None,
    )


def list_workloads() -> List[Workload]:
    base_dir = get_workloads_dir()
    if not base_dir.exists():
        return []

    workloads: List[Workload] = []
    for path in sorted(base_dir.glob("*.json")):
        workload = _load_workload(path)
        if workload:
            workloads.append(workload)
    return workloads


def get_workload(workload_id: str) -> Optional[Workload]:
    path = get_workload_path(workload_id)
    return _load_workload(path)


def _ensure_unique_name(name: str, *, exclude_id: Optional[str] = None) -> None:
    normalized = _normalize_name(name)
    for workload in list_workloads():
        if exclude_id and workload.workload_id == exclude_id:
            continue
        if _normalize_name(workload.name) == normalized:
            raise ValueError("Workload name already exists")


def create_workload(name: str, view_state: WorkloadViewState) -> Workload:
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("Workload name is required")

    _ensure_unique_name(clean_name)

    workload_id = str(uuid4())
    created_at = _now()
    workload = Workload(
        workload_id=workload_id,
        name=clean_name,
        view_state=view_state,
        created_at=created_at,
        updated_at=created_at,
        conversation_id=None,
    )
    write_json(get_workload_path(workload_id), workload.model_dump())
    return workload


def update_workload(workload_id: str, *, name: Optional[str] = None, view_state: Optional[WorkloadViewState] = None) -> Optional[Workload]:
    existing = get_workload(workload_id)
    if not existing:
        return None

    next_name = existing.name
    if name is not None:
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("Workload name is required")
        if _normalize_name(clean_name) != _normalize_name(existing.name):
            _ensure_unique_name(clean_name, exclude_id=workload_id)
        next_name = clean_name

    next_view_state = view_state or existing.view_state

    updated = Workload(
        workload_id=existing.workload_id,
        name=next_name,
        view_state=next_view_state,
        created_at=existing.created_at,
        updated_at=_now(),
        conversation_id=existing.conversation_id,
    )
    write_json(get_workload_path(workload_id), updated.model_dump())
    return updated


def delete_workload(workload_id: str) -> bool:
    path = get_workload_path(workload_id)
    if not path.exists():
        return False
    path.unlink(missing_ok=True)
    return True
