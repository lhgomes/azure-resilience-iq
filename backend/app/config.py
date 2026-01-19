from __future__ import annotations

import os
from pathlib import Path

# Centralized paths for all filesystem-backed artifacts.
# Data is organized by subscription ID: data/{subscription_id}/{filename}.json

DATA_DIR = Path(os.getenv("AZURE_WORKLOAD_GRAPH_DATA_DIR", "data"))


def get_subscription_dir(subscription_id: str) -> Path:
    """Get the base directory for a subscription."""
    return DATA_DIR / subscription_id


def get_resources_path(subscription_id: str) -> Path:
    """Get the resources.json path for a subscription."""
    return get_subscription_dir(subscription_id) / "resources.json"


def get_edges_path(subscription_id: str) -> Path:
    """Get the edges.json path for a subscription."""
    return get_subscription_dir(subscription_id) / "edges.json"


def get_llm_annotations_path(subscription_id: str) -> Path:
    """Get the llm_annotations.json path for a subscription."""
    return get_subscription_dir(subscription_id) / "llm_annotations.json"


def get_node_overrides_path(subscription_id: str) -> Path:
    """Get the node_overrides.json path for a subscription."""
    return get_subscription_dir(subscription_id) / "node_overrides.json"


def get_edge_overrides_path(subscription_id: str) -> Path:
    """Get the edge_overrides.json path for a subscription."""
    return get_subscription_dir(subscription_id) / "edge_overrides.json"


def get_groups_path(subscription_id: str) -> Path:
    """Get the groups.json path for a subscription."""
    return get_subscription_dir(subscription_id) / "groups.json"


def get_manual_edges_path(subscription_id: str) -> Path:
    """Get the manual_edges.json path for a subscription."""
    return get_subscription_dir(subscription_id) / "manual_edges.json"


def get_resilience_evaluations_path(subscription_id: str) -> Path:
    """Get the resilience_evaluations.json path for a subscription."""
    return get_subscription_dir(subscription_id) / "resilience_evaluations.json"


def get_workloads_dir() -> Path:
    """Get the base directory for saved workload views."""
    return DATA_DIR / "workload"


def get_workload_path(workload_id: str) -> Path:
    """Get the workload json path for a saved workload view."""
    return get_workloads_dir() / f"{workload_id}.json"


# Legacy: for backward compatibility during migration
COLLECTOR_DIR = DATA_DIR / "collector"
COLLECTOR_RESOURCES_PATH = COLLECTOR_DIR / "resources.json"
COLLECTOR_UNIFIED_EDGES_PATH = COLLECTOR_DIR / "unified_edges.json"
