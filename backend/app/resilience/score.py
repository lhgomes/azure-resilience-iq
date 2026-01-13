"""
Standalone Resilience Scoring Runner.

This script recalculates resilience scores from existing evaluation data
without re-running the evaluation itself.

Usage:
  python -m app.resilience.score --subscription-id <subscription-id>

This script:
1. Loads existing resilience evaluations from data/{subscription_id}/resilience_evaluations.json
2. Loads LLM annotations for component weights (optional)
3. Calculates scores using ResilienceScorer
4. Updates resilience_evaluations.json with scoring results

Requirements:
- Existing resilience_evaluations.json file (run app.resilience.run first)
"""

import json
import argparse
from pathlib import Path
from typing import Dict

from app.config import get_subscription_dir
from app.settings import load_settings, get_settings
from app.logger import setup_logging, get_logger
from app.resilience.scorer import ResilienceScorer
from app.storage.llm_annotations_store import load_llm_annotations

LOGGER = get_logger(__name__)


def main():
    """Run standalone scoring on existing evaluation data."""
    parser = argparse.ArgumentParser(
        description="Recalculate resilience scores from existing evaluations"
    )
    parser.add_argument(
        "--subscription-id",
        required=True,
        help="Azure subscription ID to score"
    )
    parser.add_argument(
        "--config",
        help="Path to settings.yaml (default: backend/config/settings.yaml)"
    )
    
    args = parser.parse_args()
    
    # Load settings (always load, optionally with custom path)
    if args.config:
        load_settings(args.config)
    else:
        load_settings()
    
    settings = get_settings()
    
    # Setup logging
    setup_logging()
    
    LOGGER.info("=" * 60)
    LOGGER.info("RESILIENCE SCORING (Standalone)")
    LOGGER.info("=" * 60)
    LOGGER.info(f"Subscription: {args.subscription_id}")
    
    try:
        # ========================================
        # Load evaluation data
        # ========================================
        evaluations_path = get_subscription_dir(args.subscription_id) / "resilience_evaluations.json"
        
        if not evaluations_path.exists():
            LOGGER.error(f"Evaluation file not found: {evaluations_path}")
            LOGGER.error("Run 'python -m app.resilience.run --subscription-id <id>' first")
            return 1
        
        LOGGER.info(f"Loading evaluations from: {evaluations_path}")
        with open(evaluations_path, 'r') as f:
            data = json.load(f)
        
        evaluations = data.get('evaluations', {})
        LOGGER.info(f"✓ Loaded {len(evaluations)} resource evaluations")
        
        # ========================================
        # Load component weights from LLM annotations
        # ========================================
        component_weights = {}
        try:
            annotations = load_llm_annotations(args.subscription_id)
            if annotations and hasattr(annotations, 'nodes'):
                for node in annotations.nodes:
                    if node.annotations.criticality_weight:
                        component_weights[node.node_id] = node.annotations.criticality_weight
                LOGGER.info(f"✓ Loaded component weights for {len(component_weights)} resources")
            else:
                LOGGER.info("No LLM annotations found - using equal weights")
        except Exception as e:
            LOGGER.warning(f"Could not load LLM annotations: {e}")
            LOGGER.info("Using equal weights for all components")
        
        # ========================================
        # Initialize scorer
        # ========================================
        category_weights = settings.get_category_weights()
        LOGGER.info("Category weights:")
        for cat, weight in category_weights.items():
            LOGGER.info(f"  {cat}: {weight}")
        
        scorer = ResilienceScorer(category_weights=category_weights)
        
        # ========================================
        # Convert evaluations to scorer format
        # ========================================
        # Build resources list with checks including impact field
        resources = []
        
        for resource_id, evaluation in evaluations.items():
            # Build checks list with impact field
            checks = []
            for check in evaluation.get('checks', []):
                impact = check.get('impact', 'Medium')  # Extract or default
                checks.append({
                    'recommendation_id': check.get('recommendation_id', ''),
                    'description': check.get('description', ''),
                    'category': check.get('category', 'Unknown'),
                    'impact': impact,
                    'status': check.get('status', 'unknown')
                })
            
            resources.append({
                'id': resource_id,
                'name': evaluation.get('resource_name', ''),
                'type': evaluation.get('resource_type', 'unknown'),
                'component_weight': evaluation.get('component_weight', 1.0),
                'checks': checks
            })
        
        scorer_input = {"resources": resources}
        
        # ========================================
        # Calculate scores
        # ========================================
        LOGGER.info("Calculating resilience scores...")
        scoring_results = scorer.score_workload(scorer_input)
        
        # ========================================
        # Save results - Update existing resilience_evaluations.json
        # ========================================
        LOGGER.info("Updating resilience_evaluations.json with scores...")
        
        # Add scoring results with new field names
        data['workload_score'] = scoring_results.get('workload_score')
        data['category_breakdown'] = scoring_results.get('category_breakdown')
        data['scoring_timestamp'] = scoring_results.get('scoring_timestamp')
        
        # Update each resource evaluation with new structure
        resources_map = {
            res['id']: res for res in scoring_results.get('resources', [])
        }
        
        for resource_id, evaluation in data['evaluations'].items():
            if resource_id in resources_map:
                resource_result = resources_map[resource_id]
                evaluation['component_score'] = resource_result.get('component_score')
                evaluation['component_weight'] = resource_result.get('component_weight')
                evaluation['categories'] = resource_result.get('categories')
                evaluation['checks_detail'] = resource_result.get('checks_detail')
        
        # Save updated data back to resilience_evaluations.json
        with open(evaluations_path, 'w') as f:
            json.dump(data, f, indent=2)
        
        LOGGER.info("=" * 60)
        LOGGER.info("SCORING COMPLETE")
        LOGGER.info("=" * 60)
        LOGGER.info(f"✓ Updated: {evaluations_path}")
        LOGGER.info("")
        LOGGER.info("Summary:")
        LOGGER.info(f"  Workload score: {scoring_results.get('workload_score', 0):.4f} (0.0-1.0 scale)")
        LOGGER.info("")
        LOGGER.info("Category Breakdown:")
        for cat, score in scoring_results.get('category_breakdown', {}).items():
            # category_breakdown returns {category_name: float_score}
            LOGGER.info(f"  {cat}: {score:.4f}")
        
        return 0
        
    except Exception as e:
        LOGGER.error(f"Error during scoring: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit(main())
