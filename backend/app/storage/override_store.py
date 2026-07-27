"""
Storage module for user overrides of resilience check statuses.

Provides persistent storage for user-submitted status overrides that should
survive collector and resilience refresh cycles.
"""

import json
import logging
import uuid
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime, timezone

LOGGER = logging.getLogger(__name__)
from app.storage._json_repo import read_json, write_json, path_exists

# Namespace UUID for azure-resilience-iq resilience checks
# Using DNS namespace as base for deterministic UUID generation
RESILIENCE_CHECK_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def generate_check_uuid(resource_id: str, recommendation_id: str) -> str:
    """
    Generate a deterministic UUIDv5 for a resource+recommendation pair.
    
    This ensures consistency across all features and allows tracking
    of overrides even after data refresh.
    
    Args:
        resource_id: Azure resource ID
        recommendation_id: APRL recommendation GUID
        
    Returns:
        Deterministic UUIDv5 string (based on resource_id|recommendation_id)
    """
    # Create a deterministic UUIDv5 from the combination
    combined = f"{resource_id}|{recommendation_id}"
    return str(uuid.uuid5(RESILIENCE_CHECK_NAMESPACE, combined))


def get_overrides_file_path(subscription_id: str) -> Path:
    """Get the path to the overrides JSON file for a subscription."""
    from app.config import get_subscription_dir
    sub_dir = get_subscription_dir(subscription_id)
    return sub_dir / "resilience_overrides.json"


def load_overrides(subscription_id: str) -> Dict[str, Dict]:
    """
    Load user overrides for a subscription.
    
    Args:
        subscription_id: Azure subscription ID
        
    Returns:
        Dictionary mapping check_uuid -> override data
        {
            "check_uuid": {
                "resource_id": "...",
                "recommendation_id": "...",
                "status": "pass" | "fail",
                "overridden_at": "ISO timestamp",
                "overridden_by": "user_identifier"
            }
        }
    """
    overrides_file = get_overrides_file_path(subscription_id)
    
    if not path_exists(overrides_file):
        LOGGER.debug(f"No overrides file found at {overrides_file}")
        return {}

    data = read_json(overrides_file, default={})
    if not isinstance(data, dict):
        return {}
    LOGGER.info(f"Loaded {len(data)} overrides for subscription {subscription_id}")
    return data


def save_override(
    subscription_id: str,
    resource_id: str,
    recommendation_id: str,
    new_status: str,
    user_identifier: str = "user"
) -> Dict:
    """
    Save a user override for a specific check.
    
    Args:
        subscription_id: Azure subscription ID
        resource_id: Azure resource ID
        recommendation_id: APRL recommendation GUID
        new_status: New status ("pass" or "fail")
        user_identifier: User who made the override (default: "user")
        
    Returns:
        The saved override data including check_uuid
    """
    check_uuid = generate_check_uuid(resource_id, recommendation_id)
    
    # Load existing overrides
    overrides = load_overrides(subscription_id)
    
    # Create/update override entry
    override_data = {
        "resource_id": resource_id,
        "recommendation_id": recommendation_id,
        "status": new_status,
        "overridden_at": datetime.now(timezone.utc).isoformat(),
        "overridden_by": user_identifier
    }
    
    overrides[check_uuid] = override_data

    overrides_file = get_overrides_file_path(subscription_id)
    write_json(overrides_file, overrides)
    LOGGER.info(f"Saved override for check {check_uuid} in subscription {subscription_id}")
    
    return {
        "check_uuid": check_uuid,
        **override_data
    }


def delete_override(subscription_id: str, check_uuid: str) -> bool:
    """
    Delete a user override.
    
    Args:
        subscription_id: Azure subscription ID
        check_uuid: UUID of the check override to delete
        
    Returns:
        True if deleted, False if not found
    """
    overrides = load_overrides(subscription_id)
    
    if check_uuid not in overrides:
        LOGGER.warning(f"Override {check_uuid} not found for deletion")
        return False
    
    del overrides[check_uuid]
    
    overrides_file = get_overrides_file_path(subscription_id)
    write_json(overrides_file, overrides)
    LOGGER.info(f"Deleted override {check_uuid} from subscription {subscription_id}")
    return True


def get_override_for_check(
    subscription_id: str,
    resource_id: str,
    recommendation_id: str
) -> Optional[Dict]:
    """
    Get the override for a specific check if it exists.
    
    Args:
        subscription_id: Azure subscription ID
        resource_id: Azure resource ID
        recommendation_id: APRL recommendation GUID
        
    Returns:
        Override data if exists, None otherwise
    """
    check_uuid = generate_check_uuid(resource_id, recommendation_id)
    overrides = load_overrides(subscription_id)
    
    return overrides.get(check_uuid)
