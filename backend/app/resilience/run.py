"""
APRL Resilience Evaluator CLI runner.

Usage:
  python -m app.resilience.run --subscription-id ebb79bc0-aa86-44a7-8111-cabbe0c43993

This script:
1. Loads the resources from collector output (data/{subscription_id}/resources.json)
2. Loads LLM annotations for criticality weights (data/{subscription_id}/llm_annotations.json)
3. Runs APRL evaluator against all resources
4. Saves evaluation results to data/{subscription_id}/resilience_evaluations.json
5. Reports success/failures

Requires:
- collector output file (from app.collector.run)
- APRL catalog available at backend/aprl/

Paths:
- Override the base data directory with AZURE_WORKLOAD_GRAPH_DATA_DIR (default: data)
"""

import json
import argparse
import os
from collections import defaultdict
from typing import Dict, Optional, List
from pathlib import Path

from dotenv import load_dotenv

from app.config import get_resources_path
from app.settings import get_settings, load_settings
from app.logger import setup_logging, get_logger
from app.resilience.aprl_integration import load_aprl_catalog, APRLEvaluator
from app.resilience.scorer import ResilienceScorer
from app.storage.resilience_evaluations_store import save_resilience_evaluations
from app.storage.llm_annotations_store import load_llm_annotations

# Load environment variables from .env file
# Look for .env in the backend directory (parent of app/)
_backend_dir = Path(__file__).parent.parent.parent
_env_file = _backend_dir / ".env"
load_dotenv(_env_file)

LOGGER = get_logger(__name__)


def get_aoai_client(use_real_llm: bool) -> Optional[object]:
    """
    Initialize Azure OpenAI client if LLM is enabled.
    
    Uses Azure AD authentication (DefaultAzureCredential) if no API key is provided.
    This allows running in user login context without storing API keys.
    
    Args:
        use_real_llm: Whether to enable real LLM (from config)
        
    Returns:
        AzureOpenAI client or None if disabled or credentials missing
    """
    if not use_real_llm:
        return None
    
    try:
        from openai import AzureOpenAI
        from azure.identity import DefaultAzureCredential
    except ImportError:
        LOGGER.warning("OpenAI or azure-identity package not installed. LLM disabled.")
        return None
    
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    
    if not endpoint or not deployment:
        LOGGER.warning(
            "LLM enabled but AZURE_OPENAI_ENDPOINT or AZURE_OPENAI_DEPLOYMENT not set. LLM disabled."
        )
        return None
    
    try:
        api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-05-01-preview")
        timeout_seconds = int(os.getenv("AZURE_OPENAI_TIMEOUT_SECONDS", "60"))
        
        # Prefer API key if provided; otherwise use Azure AD (DefaultAzureCredential)
        api_key = os.getenv("AZURE_OPENAI_KEY")
        if api_key:
            client = AzureOpenAI(
                azure_endpoint=endpoint,
                api_key=api_key,
                api_version=api_version,
                timeout=timeout_seconds,
            )
            LOGGER.info("✓ Azure OpenAI client initialized with API key")
        else:
            credential = DefaultAzureCredential()
            token = credential.get_token("https://cognitiveservices.azure.com/.default")
            client = AzureOpenAI(
                azure_endpoint=endpoint,
                azure_ad_token=token.token,
                api_version=api_version,
                timeout=timeout_seconds,
            )
            LOGGER.info("✓ Azure OpenAI client initialized with Azure AD authentication")
        
        return client
    except Exception as e:
        LOGGER.warning("Failed to initialize Azure OpenAI client: %s. LLM disabled.", e)
        return None




def get_criticality_weights(subscription_id: str) -> dict:
    """Load criticality weights from LLM annotations."""
    try:
        annotations = load_llm_annotations(subscription_id)
        weights = {}
        for node in annotations.nodes:
            if node.node_id and node.annotations:
                weights[node.node_id] = node.annotations.criticality_weight or 1.0
        return weights
    except Exception as e:
        LOGGER.warning("Could not load LLM annotations: %s. Using default weights.", e)
        return {}


def main():
    parser = argparse.ArgumentParser(
        description="Run APRL resilience evaluator on collected resources"
    )
    parser.add_argument(
        "--subscription-id",
        required=True,
        help="Subscription ID (UUID format)"
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Override log level (default: from config or INFO)",
    )
    args = parser.parse_args()

    LOGGER.info("Starting APRL resilience evaluator for subscription: %s", args.subscription_id)

    # Check if resources exist
    resources_path = get_resources_path(args.subscription_id)
    if not resources_path.exists():
        LOGGER.error(
            "Collector resources not found at %s. "
            "Run 'python -m app.collector.run --subscription-id %s' first.",
            resources_path, args.subscription_id
        )
        return 1

    try:
        # Load settings and configure logging with optional CLI override
        load_settings()
        settings = get_settings()
        setup_logging(args.log_level)
        
        # Initialize Azure OpenAI client if LLM is enabled
        use_real_llm = settings.use_real_llm()
        aoai_client = get_aoai_client(use_real_llm)
        if use_real_llm and not aoai_client:
            LOGGER.warning("LLM was enabled in config but could not initialize. Using heuristics only.")
        
        LOGGER.info("Loading APRL catalog...")
        catalog = load_aprl_catalog(settings)
        summary = catalog.get_summary()
        LOGGER.info("✓ APRL catalog loaded: %d recommendations", summary["total_recommendations"])

        # Load resources
        LOGGER.debug("Loading resources from %s", resources_path)
        resources_data = json.loads(resources_path.read_text())
        
        # Handle new format with subscription metadata
        if isinstance(resources_data, dict) and "resources" in resources_data:
            resources = resources_data["resources"]
        else:
            # Fallback for direct list format
            resources = resources_data
        
        LOGGER.info("Loaded %d resources", len(resources))

        # Load criticality weights from LLM annotations
        LOGGER.debug("Loading LLM annotations for criticality weights...")
        annotations = load_llm_annotations(args.subscription_id)
        criticality_weights = get_criticality_weights(args.subscription_id)
        LOGGER.debug("Loaded criticality weights for %d resources", len(criticality_weights))

        # Create evaluator with category weights from settings and optional LLM client
        category_weights = settings.get_category_weights()
        evaluator = APRLEvaluator(catalog, category_weights, aoai_client=aoai_client)

        # Evaluate all resources (one KQL per recommendation per resource type)
        LOGGER.info("Running resilience evaluation on %d resources...", len(resources))
        evaluations = {}
        detail_log = []

        resources_by_type: Dict[str, List[dict]] = defaultdict(list)
        for res in resources:
            rtype = res.get("type")
            if not rtype:
                LOGGER.warning("Skipping resource with missing type")
                continue
            resources_by_type[rtype].append(res)

        for rtype, res_list in resources_by_type.items():
            batch_results = evaluator.evaluate_resources_batch(
                resource_type=rtype,
                resources=res_list,
                criticality_weights=criticality_weights,
                subscription_id=args.subscription_id,
                detail_log=detail_log,
            )

            for resource_id, result in batch_results.items():
                failed_checks = [f for f in result.checks if f.get("status") == "fail"]
                passed_checks = [f for f in result.checks if f.get("status") == "pass"]
                pending_checks = [f for f in result.checks if f.get("status") == "pending"]
                evaluations[resource_id] = {
                    "resource_id": result.resource_id,
                    "resource_type": result.resource_type,
                    "resource_name": result.resource_name,
                    "total_checks": len(result.checks),
                    "failed_checks": len(failed_checks),
                    "passed_checks": len(passed_checks),
                    "pending_checks": len(pending_checks),
                    "checks": result.checks,
                }

        # Evaluate subscription-level recommendations
        LOGGER.info("Evaluating subscription-level recommendations...")
        subscription_recs = catalog.get_recommendations_by_resource_type(
            "Microsoft.Subscription/subscriptions"
        )
        
        if subscription_recs:
            sub_resource = {
                "id": f"/subscriptions/{args.subscription_id}",
                "name": args.subscription_id,
                "type": "Microsoft.Subscription/subscriptions"
            }
            
            sub_results = evaluator.evaluate_resources_batch(
                resource_type="Microsoft.Subscription/subscriptions",
                resources=[sub_resource],
                criticality_weights={sub_resource["id"]: 1.0},
                subscription_id=args.subscription_id,
                detail_log=detail_log,
            )
            
            for resource_id, result in sub_results.items():
                failed_checks = [f for f in result.checks if f.get("status") == "fail"]
                passed_checks = [f for f in result.checks if f.get("status") == "pass"]
                pending_checks = [f for f in result.checks if f.get("status") == "pending"]
                evaluations[resource_id] = {
                    "resource_id": result.resource_id,
                    "resource_type": result.resource_type,
                    "resource_name": result.resource_name,
                    "total_checks": len(result.checks),
                    "failed_checks": len(failed_checks),
                    "passed_checks": len(passed_checks),
                    "pending_checks": len(pending_checks),
                    "checks": result.checks,
                }
            
            LOGGER.info(f"✓ Added {len(sub_results)} subscription-level evaluations")

        LOGGER.debug("Evaluation complete: %d resources evaluated", len(evaluations))

        # Save evaluations (internal format)
        LOGGER.debug("Saving evaluation results...")
        save_resilience_evaluations(args.subscription_id, evaluations)

        # Save detailed evaluation log with executed queries and responses
        from datetime import datetime
        from app.config import get_subscription_dir
        
        detailed_path = get_subscription_dir(args.subscription_id) / "resilience_evaluations_detailed.json"
        detailed_payload = {
            "subscription_id": args.subscription_id,
            "recommendation_runs": detail_log,
        }
        with open(detailed_path, "w") as f:
            json.dump(detailed_payload, f, indent=2)

        # ========================================
        # SCORING: Calculate resilience scores
        # ========================================
        LOGGER.info("Calculating resilience scores...")
        
        # Get category weights from settings
        category_weights = settings.get_category_weights()
        
        # Get component weights from LLM annotations (criticality_weight)
        component_weights = {}
        if annotations and hasattr(annotations, 'nodes'):
            for node in annotations.nodes:
                if node.annotations.criticality_weight:
                    component_weights[node.node_id] = node.annotations.criticality_weight
        
        # Initialize scorer with new hierarchical model
        # Note: component_weights are now per-resource in data format
        scorer = ResilienceScorer(category_weights=category_weights)
        
        # Convert evaluations to new scorer format
        # New format uses: check → category → resource → workload hierarchy
        scorer_input = {
            "resources": []
        }
        
        for resource_id, evaluation in evaluations.items():
            # Pass complete original checks to scorer for enrichment
            # Scorer will preserve all APRL data and add scoring fields
            checks = evaluation.get('checks', [])
            
            # Get component_weight from LLM annotations (criticality_weight)
            component_weight = component_weights.get(resource_id, 1.0)
            
            scorer_input['resources'].append({
                'id': resource_id,
                'name': evaluation.get('resource_name'),
                'type': evaluation.get('resource_type'),
                'component_weight': component_weight,
                'checks': checks
            })
        
        # Calculate scores using new hierarchical model
        scoring_results = scorer.score_workload(scorer_input)
        
        # Save scoring results - Update resilience_evaluations.json
        LOGGER.info("Adding scores to resilience evaluations...")
        
        # Read the current evaluation file
        from app.config import get_subscription_dir
        evaluations_path = get_subscription_dir(args.subscription_id) / "resilience_evaluations.json"
        
        with open(evaluations_path, 'r') as f:
            eval_data = json.load(f)
        
        # Add new scoring results to the data structure
        eval_data['workload_score'] = scoring_results.get('workload_score')
        eval_data['category_breakdown'] = scoring_results.get('category_breakdown')
        eval_data['scoring_timestamp'] = scoring_results.get('timestamp')
        
        # Update each resource evaluation with its new scoring details
        resources_score_map = {
            rs['id']: rs for rs in scoring_results.get('resources', [])
        }
        
        for resource_id in eval_data['evaluations'].keys():
            if resource_id in resources_score_map:
                res_score = resources_score_map[resource_id]
                # Update with new format: component_score (0-1.0) + details
                eval_data['evaluations'][resource_id]['component_score'] = res_score.get('component_score')
                eval_data['evaluations'][resource_id]['component_weight'] = res_score.get('component_weight')
                eval_data['evaluations'][resource_id]['categories'] = res_score.get('categories', [])
                # Use merged checks field (enriched with scoring data) - no separate checks_detail
                eval_data['evaluations'][resource_id]['checks'] = res_score.get('checks', [])
                
                # Remove old fields if present
                if 'overall_score' in eval_data['evaluations'][resource_id]:
                    del eval_data['evaluations'][resource_id]['overall_score']
                if 'checks_detail' in eval_data['evaluations'][resource_id]:
                    del eval_data['evaluations'][resource_id]['checks_detail']
                if 'scores' in eval_data['evaluations'][resource_id]:
                    del eval_data['evaluations'][resource_id]['scores']
        
        # Save updated data back
        with open(evaluations_path, 'w') as f:
            json.dump(eval_data, f, indent=2)
        
        LOGGER.info(f"✓ Resilience scores added to: {evaluations_path}")
        LOGGER.info(f"  Workload score: {scoring_results.get('workload_score', 0):.2%}")

        # Print summary
        total_checks = sum(e.get("total_checks", 0) for e in evaluations.values())
        total_failed = sum(e.get("failed_checks", 0) for e in evaluations.values())
        total_passed = sum(e.get("passed_checks", 0) for e in evaluations.values())
        total_pending = sum(e.get("pending_checks", 0) for e in evaluations.values())
        LOGGER.info(
            "✓ Resilience evaluation complete: %d resources, %d total checks (%d passed, %d failed, %d pending review)",
            len(evaluations), total_checks, total_passed, total_failed, total_pending
        )
        return 0

    except Exception as e:
        LOGGER.error("Error during evaluation: %s", e, exc_info=True)
        return 1


if __name__ == "__main__":
    exit(main())
