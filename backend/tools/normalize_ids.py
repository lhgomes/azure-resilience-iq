#!/usr/bin/env python3
"""
Script to manually normalize all ID fields in resources.json and edges.json to lowercase.
"""

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List

def norm_id(resource_id: str) -> str:
    """Normalize resource ID to lowercase."""
    return (resource_id or "").strip().lower()


def short_id(resource_id: str) -> str:
    """Deterministic compact ID derived from the canonical ARM ID."""
    rid = norm_id(resource_id)
    if not rid:
        return ""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, rid))


def is_azure_resource_id(value: str) -> bool:
    """
    Detect if a string is an Azure resource ID.
    Azure IDs start with /subscriptions/ and contain the resource structure.
    
    Args:
        value: String to check
        
    Returns:
        True if value is an Azure resource ID, False otherwise
    """
    if not isinstance(value, str):
        return False
    normalized = (value or "").strip().lower()
    return normalized.startswith('/subscriptions/')


def normalize_id_fields(data: Any) -> Any:
    """
    Recursively normalize all Azure resource IDs in a data structure to lowercase.
    Identifies Azure IDs by pattern (starts with /subscriptions/) rather than field name.
    This catches all Azure IDs regardless of field name: id, parentResourceId, failoverGroupId, etc.
    
    Handles strings, dicts, and lists.
    """
    if isinstance(data, dict):
        normalized = {}
        for key, value in data.items():
            if isinstance(value, str) and is_azure_resource_id(value):
                # Normalize any Azure resource ID, regardless of field name
                normalized[key] = norm_id(value)
            else:
                # Recursively normalize nested structures
                normalized[key] = normalize_id_fields(value)
        return normalized
    elif isinstance(data, list):
        return [normalize_id_fields(item) for item in data]
    else:
        return data


def normalize_resources(resources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize all ID fields in resources."""
    normalized = []
    
    for resource in resources:
        # Normalize all id fields recursively in properties
        resource = normalize_id_fields(resource)
        
        # Ensure main fields are normalized
        if 'id' in resource:
            resource['id'] = norm_id(resource['id'])
            resource['short_id'] = short_id(resource['id'])
        
        # Normalize parent and related id fields
        if resource.get('parent_resource_id'):
            resource['parent_resource_id'] = norm_id(resource['parent_resource_id'])
        if resource.get('backend_pool_ids'):
            resource['backend_pool_ids'] = [norm_id(bid) for bid in resource['backend_pool_ids']]
        if resource.get('failover_group_id'):
            resource['failover_group_id'] = norm_id(resource['failover_group_id'])
        if resource.get('child_resource_ids'):
            resource['child_resource_ids'] = [norm_id(cid) for cid in resource['child_resource_ids']]
        
        normalized.append(resource)
    
    return normalized


def normalize_edges(edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize all ID fields in edges."""
    normalized = []
    
    for edge in edges:
        # Normalize main edge fields
        if edge.get('source'):
            edge['source'] = norm_id(edge['source'])
        if edge.get('target'):
            edge['target'] = norm_id(edge['target'])
        
        # Just normalize the existing id - don't regenerate it
        if edge.get('id'):
            edge['id'] = norm_id(edge['id'])
        
        normalized.append(edge)
    
    return normalized


def main():
    subscription_id = "59e12ca5-d654-418e-bc74-ef6f56c92836"
    backend_dir = Path(__file__).resolve().parents[1]
    data_dir = backend_dir / "data" / subscription_id
    resources_file = data_dir / "resources.json"
    edges_file = data_dir / "edges.json"
    
    # Normalize resources
    if resources_file.exists():
        print(f"📖 Loading resources from {resources_file}...")
        with open(resources_file) as f:
            data = json.load(f)
        
        subscription_name = data.get("subscription_name", subscription_id)
        resources = data.get("resources", [])
        
        print(f"📝 Normalizing {len(resources)} resources...")
        normalized_resources = normalize_resources(resources)
        
        # Save normalized resources
        output_data = {
            "subscription_id": subscription_id,
            "subscription_name": subscription_name,
            "resources": normalized_resources
        }
        
        with open(resources_file, 'w') as f:
            json.dump(output_data, f, indent=2)
        
        print(f"✅ Normalized resources saved to {resources_file}")
    else:
        print(f"❌ Resources file not found: {resources_file}")
    
    # Normalize edges
    if edges_file.exists():
        print(f"\n📖 Loading edges from {edges_file}...")
        with open(edges_file) as f:
            data = json.load(f)
        
        subscription_name = data.get("subscription_name", subscription_id)
        edges = data.get("edges", [])
        
        print(f"📝 Normalizing {len(edges)} edges...")
        normalized_edges = normalize_edges(edges)
        
        # Save normalized edges
        output_data = {
            "subscription_id": subscription_id,
            "subscription_name": subscription_name,
            "edges": normalized_edges
        }
        
        with open(edges_file, 'w') as f:
            json.dump(output_data, f, indent=2)
        
        print(f"✅ Normalized edges saved to {edges_file}")
    else:
        print(f"⚠️ Edges file not found: {edges_file}")


if __name__ == "__main__":
    main()

