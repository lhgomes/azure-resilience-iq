import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml
from app.settings import load_settings

LOGGER = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "monitored_resource_types.yaml"
)


def _load_raw_config(target: Path) -> Any:
    """Load config content from YAML or JSON based on extension."""
    text = target.read_text()
    if target.suffix.lower() in {".yaml", ".yml"}:
        return yaml.safe_load(text)
    return json.loads(text)


def load_monitored_resource_types(config_path: Optional[str] = None) -> Set[str]:
    """Load the monitored resource type allowlist (HA/DR-focused).

    Args:
        config_path: Optional explicit path to the allowlist file.

    Returns:
        Set of lowercase resource type strings. Empty set if the file is missing
        or cannot be parsed.
    """
    if config_path:
        target = Path(config_path)
    else:
        settings = load_settings()
        target = Path(settings.get_monitored_resource_types_path() or DEFAULT_CONFIG_PATH)

    try:
        raw = _load_raw_config(target)
    except FileNotFoundError:
        LOGGER.warning("HA/DR resource type config not found at %s; no filtering applied", target)
        return set()
    except Exception as exc:  # pragma: no cover - defensive logging
        LOGGER.error("Failed to read HA/DR resource type config at %s: %s", target, exc)
        return set()

    entries: List[str] | Any
    if isinstance(raw, dict):
        entries = (
            raw.get("monitored_resource_types")
            or raw.get("ha_dr_resource_types")
            or raw.get("resource_types")
            or raw.get("ha_dr")
            or []
        )
    else:
        entries = raw

    if not isinstance(entries, list):
        LOGGER.warning("HA/DR resource type config at %s is not a list; skipping filter", target)
        return set()

    return {str(item).lower() for item in entries if item}


def filter_resources_by_type(
    resources: List[Dict[str, Any]], allowed_types: Set[str]
) -> Tuple[List[Dict[str, Any]], Set[str]]:
    """Filter resources by allowlisted types.

    Args:
        resources: List of resource dictionaries with a `type` field.
        allowed_types: Lowercase allowlist of resource types.

    Returns:
        Tuple of (filtered_resources, kept_ids)
    """
    if not allowed_types:
        ids = {r.get("id") for r in resources if r.get("id")}
        return resources, ids

    filtered: List[Dict[str, Any]] = []
    kept_ids: Set[str] = set()
    for res in resources:
        res_type = str(res.get("type", "")).lower()
        if res_type in allowed_types:
            filtered.append(res)
            if res.get("id"):
                kept_ids.add(res["id"])

    return filtered, kept_ids


def filter_edges_by_ids(edges: List[Dict[str, Any]], valid_ids: Set[str]) -> List[Dict[str, Any]]:
    """Keep edges whose endpoints are retained resources."""
    if not valid_ids:
        return edges

    return [
        edge
        for edge in edges
        if edge.get("source") in valid_ids and edge.get("target") in valid_ids
    ]
