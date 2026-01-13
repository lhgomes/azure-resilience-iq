"""
Resilience Evaluations Storage Module

Provides functions to load and save APRL resilience evaluation results.
Results are stored as JSON in data/{subscription_id}/resilience_evaluations.json

User overrides are applied when loading evaluations to ensure consistency.
"""

from pathlib import Path
from typing import Dict, Any
import logging

from app.storage._json_repo import read_json, write_json
from app.config import get_resilience_evaluations_path

LOGGER = logging.getLogger(__name__)


def _path(subscription_id: str) -> Path:
    return get_resilience_evaluations_path(subscription_id)


def load_resilience_evaluations(subscription_id: str, apply_overrides: bool = True) -> Dict[str, Any]:
    """Load resilience evaluation results from storage.
    
    Handles both new and old data formats for backward compatibility:
    - New format: has workload_score, category_breakdown, resources with impact fields
    - Old format: has overall_score, category_scores, component_scores
    
    Args:
        subscription_id: Azure subscription ID
        apply_overrides: Whether to apply user overrides (default: True)
    
    Returns:
        Dictionary with evaluations in new format, with overrides applied
    """
    data = read_json(_path(subscription_id), default={})
    
    # If empty or already in new format, continue
    if not data or "workload_score" in data:
        pass
    # Handle backward compatibility: convert old format to new
    elif "overall_score" in data:
        data["workload_score"] = data.pop("overall_score", 0)
        if "category_scores" in data and "category_breakdown" not in data:
            data["category_breakdown"] = data.pop("category_scores", {})
    
    # Apply user overrides if requested
    if apply_overrides and data:
        try:
            from app.storage.override_store import load_overrides, generate_check_uuid
            overrides = load_overrides(subscription_id)
            
            if overrides:
                # Apply overrides to findings/checks in each resource evaluation
                evaluations = data.get("evaluations", {})
                for resource_id, evaluation in evaluations.items():
                    findings = evaluation.get("findings") or evaluation.get("checks", [])
                    
                    for finding in findings:
                        recommendation_id = finding.get("recommendation_id")
                        if recommendation_id:
                            check_uuid = generate_check_uuid(resource_id, recommendation_id)
                            override = overrides.get(check_uuid)
                            
                            if override:
                                # Apply override status and mark as user-overridden
                                finding["status"] = override["status"]
                                finding["validation_source"] = "User"
                                finding["overridden_at"] = override.get("overridden_at")
                                LOGGER.debug(f"Applied override for check {check_uuid}: {override['status']}")
                
                LOGGER.info(f"Applied {len(overrides)} overrides to evaluations for subscription {subscription_id}")
        except Exception as e:
            LOGGER.error(f"Failed to apply overrides: {e}")
            # Continue without overrides rather than failing
    
    return data


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
