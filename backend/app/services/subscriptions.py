"""Service to enumerate available subscriptions from the data directory."""
import json
import logging
import re
from pathlib import Path
from typing import TypedDict

from app.config import DATA_DIR

LOGGER = logging.getLogger(__name__)

# UUID v4 pattern for subscription IDs
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE
)


class SubscriptionInfo(TypedDict):
    id: str
    name: str
    resource_count: int
    conversation_id: str | None


def list_subscriptions() -> list[SubscriptionInfo]:
    """
    Scan the data directory for subscription folders and extract metadata.
    Returns a list of {id, name} dicts.
    """
    if not DATA_DIR.exists():
        LOGGER.warning(f"Data directory does not exist: {DATA_DIR}")
        return []

    subscriptions: list[SubscriptionInfo] = []

    for item in DATA_DIR.iterdir():
        if not item.is_dir():
            continue
        
        # Only process directories with valid UUID names (subscription IDs)
        if not UUID_PATTERN.match(item.name):
            continue

        subscription_id = item.name
        resources_file = item / "resources.json"

        if not resources_file.exists():
            LOGGER.debug(f"Skipping {subscription_id}: no resources.json found")
            continue

        try:
            with resources_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
                subscription_name = data.get("subscription_name") or subscription_id
                resource_count = len(data.get("resources", [])) if isinstance(data.get("resources"), list) else 0
                conversation_id = data.get("conversation_id")
        except Exception as e:
            LOGGER.warning(f"Failed to read resources.json for {subscription_id}: {e}")
            subscription_name = subscription_id
            resource_count = 0
            conversation_id = None

        subscriptions.append(
            {
                "id": subscription_id,
                "name": subscription_name,
                "resource_count": int(resource_count),
                "conversation_id": str(conversation_id).strip() if conversation_id else None,
            }
        )

    # Sort by name for consistent ordering
    subscriptions.sort(key=lambda s: s["name"])
    return subscriptions
