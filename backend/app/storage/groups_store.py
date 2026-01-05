from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel

from app.storage._json_repo import DATA_DIR, read_json, write_json

BASE = DATA_DIR / "groups"


class NodeGroup(BaseModel):
    id: str
    name: str
    nodes: List[str]


def _path(workload_id: str) -> Path:
    return BASE / f"{workload_id}.json"


def load_groups(workload_id: str) -> List[NodeGroup]:
    raw = read_json(_path(workload_id), default=[])
    if not isinstance(raw, list):
        return []
    return [NodeGroup(**item) for item in raw if isinstance(item, dict)]


def get_group(workload_id: str, group_id: str) -> Optional[NodeGroup]:
    groups = load_groups(workload_id)
    for group in groups:
        if group.id == group_id:
            return group
    return None


def save_group(workload_id: str, group: NodeGroup):
    groups = load_groups(workload_id)
    
    # Replace existing group with same id or add new one
    groups = [g for g in groups if g.id != group.id]
    groups.append(group)
    
    write_json(_path(workload_id), [g.model_dump() for g in groups])


def delete_group(workload_id: str, group_id: str) -> bool:
    groups = load_groups(workload_id)
    new_groups = [g for g in groups if g.id != group_id]
    
    if len(new_groups) == len(groups):
        return False
    
    write_json(_path(workload_id), [g.model_dump() for g in new_groups])
    return True


def add_node_to_group(workload_id: str, group_id: str, node_id: str) -> bool:
    group = get_group(workload_id, group_id)
    if not group:
        return False
    
    if node_id not in group.nodes:
        group.nodes.append(node_id)
        save_group(workload_id, group)
    
    return True


def remove_node_from_group(workload_id: str, group_id: str, node_id: str) -> bool:
    group = get_group(workload_id, group_id)
    if not group:
        return False
    
    if node_id in group.nodes:
        group.nodes.remove(node_id)
        save_group(workload_id, group)
    
    return True
