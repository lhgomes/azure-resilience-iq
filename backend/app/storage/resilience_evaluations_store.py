"""
Resilience Evaluations Storage Module

Provides functions to load and save APRL resilience evaluation results.
Results are stored as JSON in data/{subscription_id}/resilience_evaluations.json
"""

from pathlib import Path
from typing import Dict, Any

from app.storage._json_repo import read_json, write_json
from app.config import get_resilience_evaluations_path


def _path(subscription_id: str) -> Path:
    return get_resilience_evaluations_path(subscription_id)


def load_resilience_evaluations(subscription_id: str) -> Dict[str, Any]:
    """Load resilience evaluation results from storage.
    
    Returns:
        Dictionary mapping resource_id to evaluation result
    """
    return read_json(_path(subscription_id), default={})


def save_resilience_evaluations(subscription_id: str, evaluations: Dict[str, Any]):
    """Save resilience evaluation results to storage.
    
    Args:
        subscription_id: Azure subscription ID
        evaluations: Dictionary mapping resource_id to evaluation result
    """
    payload = {
        "subscription_id": subscription_id,
        "evaluations": evaluations,
    }
    write_json(_path(subscription_id), payload)
