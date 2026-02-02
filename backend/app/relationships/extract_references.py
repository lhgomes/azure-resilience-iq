"""
Generic resource reference scanner using configuration-driven approach.

Finds resource references based on REFERENCE_DEFINITIONS, which define
where in the resource properties to find references to other resources.
"""

from typing import Any, Dict, List, Tuple
from .reference_definitions import REFERENCE_DEFINITIONS, get_references_for_source_type
from .utils import norm_id, is_azure_resource_id


def _get_nested_value(obj: Any, path: str) -> List[str]:
    """
    Extract values from nested object using dot/bracket notation.
    
    Examples:
        "properties.subnet.id" -> extracts from obj['properties']['subnet']['id']
        "properties.items[].id" -> extracts [id] from each item in obj['properties']['items']
        "ipConfigurations[].properties.pools[].id" -> handles nested arrays
    
    Returns:
        List of found values (strings)
    """
    values = []
    
    # Handle array notation (e.g., "items[].id")
    if "[]" in path:
        parts = path.split("[]", 1)  # Split only on FIRST occurrence
        # Navigate to the array
        current = obj
        for part in parts[0].split("."):
            if part and isinstance(current, dict):
                current = current.get(part)
        
        # If we found an array, extract from each item
        if isinstance(current, list):
            remaining_path = parts[1].lstrip(".")
            for item in current:
                sub_values = _get_nested_value(item, remaining_path) if remaining_path else [item]
                values.extend(sub_values)
        
        return values
    
    # Simple nested path (e.g., "properties.subnet.id")
    current = obj
    for part in path.split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return []
    
    if current is not None:
        if isinstance(current, list):
            values.extend([v for v in current if isinstance(v, str)])
        elif isinstance(current, str):
            values.append(current)
    
    return values


def extract_resource_references(
    resources_by_id: Dict[str, Dict[str, Any]]
) -> List[Tuple[str, str, str, str, float, list]]:
    """
    Extract resource references using configuration-driven definitions.
    
    Uses REFERENCE_DEFINITIONS to find known reference patterns.
    
    Returns:
        List of edge tuples: (source_id, target_id, relationship, signal_type, confidence, evidence)
    """
    edges: List[Tuple[str, str, str, str, float, list]] = []
    
    for source_id, resource in resources_by_id.items():
        source_type = (resource.get("type") or "").lower()
        
        # Get all reference definitions for this source type
        reference_defs = get_references_for_source_type(source_type)
        
        for ref_def in reference_defs:
            props = resource.get("properties") or {}
            
            # Extract reference values from the configured path
            ref_values = _get_nested_value(props, ref_def["reference_field"])
            
            if not ref_values:
                continue
            
            for ref_value in ref_values:
                # Normalize and validate the reference is an Azure ID
                if not isinstance(ref_value, str) or not is_azure_resource_id(ref_value):
                    continue
                
                target_id = norm_id(ref_value)
                
                # Special handling for backend pool IDs - extract parent LB/AppGW ID
                # Backend pool IDs look like: /loadbalancers/xxx/backendaddresspools/yyy
                # We want the parent: /loadbalancers/xxx
                if '/backendaddresspools/' in target_id:
                    target_id = '/'.join(target_id.split('/backendaddresspools/')[0].split('/'))
                
                # Special handling for IP configuration IDs - extract parent NIC ID
                # IP config IDs look like: /networkinterfaces/xxx/ipconfigurations/yyy
                # We want the parent: /networkinterfaces/xxx
                if '/ipconfigurations/' in target_id:
                    target_id = '/'.join(target_id.split('/ipconfigurations/')[0].split('/'))
                
                # If target_type is specified, validate the target matches
                target_type_filter = ref_def.get("target_type")
                if target_type_filter:
                    target_resource = resources_by_id.get(target_id)
                    if not target_resource:
                        continue
                    target_res_type = (target_resource.get("type") or "").lower()
                    if (target_type_filter or "").lower() not in target_res_type:
                        continue
                
                # Create edge
                edge = (
                    source_id,
                    target_id,
                    ref_def["relationship"],
                    "ARM_Declared",
                    ref_def["confidence"],
                    [
                        {
                            "field": ref_def["reference_field"],
                            "value": ref_value,
                            "source": "reference_definition"
                        }
                    ]
                )
                edges.append(edge)
    
    return edges
