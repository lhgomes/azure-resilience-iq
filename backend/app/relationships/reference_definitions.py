"""
Configuration loader for Azure resource reference definitions.

Loads reference patterns from YAML configuration file.
Users can extend config/reference_definitions.yaml to add new reference patterns.
"""

from typing import List, Dict, Any
from pathlib import Path
import yaml

# Path to YAML configuration file (in backend/config/)
CONFIG_FILE = Path(__file__).parent.parent.parent / "config" / "reference_definitions.yaml"

# Cached definitions (loaded once on import)
_REFERENCE_DEFINITIONS: List[Dict[str, Any]] = None


def _load_definitions() -> List[Dict[str, Any]]:
    """Load reference definitions from YAML file."""
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Reference definitions file not found: {CONFIG_FILE}")
    
    with open(CONFIG_FILE, 'r') as f:
        definitions = yaml.safe_load(f)
    
    if not isinstance(definitions, list):
        raise ValueError("Reference definitions YAML must contain a list of definitions")
    
    return definitions


def get_all_definitions() -> List[Dict[str, Any]]:
    """Get all reference definitions (cached after first load)."""
    global _REFERENCE_DEFINITIONS
    if _REFERENCE_DEFINITIONS is None:
        _REFERENCE_DEFINITIONS = _load_definitions()
    return _REFERENCE_DEFINITIONS


# Backwards compatibility: expose as module-level variable
REFERENCE_DEFINITIONS = get_all_definitions()

def get_references_for_source_type(source_type: str) -> List[Dict[str, Any]]:
    """Get all reference definitions for a given source resource type."""
    source_type_normalized = (source_type or "").lower()
    definitions = get_all_definitions()
    return [
        ref for ref in definitions
        if (ref.get("source_type") or "").lower() == source_type_normalized
    ]


def validate_definitions() -> bool:
    """Validate that all reference definitions are well-formed."""
    required_fields = {"source_type", "relationship", "reference_field", "confidence"}
    definitions = get_all_definitions()
    
    for i, ref in enumerate(definitions):
        if not all(field in ref for field in required_fields):
            raise ValueError(f"Reference definition {i} missing required fields: {required_fields}")
        
        if not isinstance(ref.get("confidence"), (int, float)) or not (0 <= ref["confidence"] <= 1):
            raise ValueError(f"Reference definition {i} has invalid confidence: {ref['confidence']}")
        
        if ref.get("is_array") not in [True, False]:
            raise ValueError(f"Reference definition {i} has invalid is_array: {ref.get('is_array')}")
    
    return True


# Validate on import
validate_definitions()
