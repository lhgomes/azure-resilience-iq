"""
APRL Resilience Evaluator CLI runner.

Usage:
  python -m app.resilience.run --subscription-id 00000000-0000-0000-0000-000000000000

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
from typing import Dict, Optional
from pathlib import Path

from dotenv import load_dotenv

from app.config import get_resources_path
from app.settings import get_settings, load_settings
from app.logger import setup_logging, get_logger
from app.resilience.aprl_integration import load_aprl_catalog, APRLEvaluator
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
                evaluations[resource_id] = {
                    "resource_id": result.resource_id,
                    "resource_type": result.resource_type,
                    "resource_name": result.resource_name,
                    "total_checks": len(result.checks),
                    "failed_checks": len(failed_checks),
                    "passed_checks": len(result.checks) - len(failed_checks),
                    "scores": result.scores,
                    "overall_score": result.overall_score,
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
                evaluations[resource_id] = {
                    "resource_id": result.resource_id,
                    "resource_type": result.resource_type,
                    "resource_name": result.resource_name,
                    "total_checks": len(result.checks),
                    "failed_checks": len(failed_checks),
                    "passed_checks": len(result.checks) - len(failed_checks),
                    "scores": result.scores,
                    "overall_score": result.overall_score,
                    "checks": result.checks,
                }
            
            LOGGER.info(f"✓ Added {len(sub_results)} subscription-level evaluations")

        LOGGER.debug("Evaluation complete: %d resources evaluated", len(evaluations))

        # Save evaluations (internal format)
        LOGGER.debug("Saving evaluation results...")
        save_resilience_evaluations(args.subscription_id, evaluations)

        # Also save in WARA-compatible format for API compatibility
        from datetime import datetime
        from app.config import get_subscription_dir
        
        wara_format = {
            "version": "1.0",
            "generated": datetime.now().isoformat() + "Z",
            "subscriptionId": args.subscription_id,
            "recommendations": []  # WARA CLI uses "recommendations" array
        }
        
        # Convert findings to WARA advisory format (only failed checks for WARA compatibility)
        for resource_id, evaluation in evaluations.items():
            for check in evaluation.get('checks', []):
                # Only include failed checks in WARA format
                if check.get('status') == 'fail':
                    wara_format['recommendations'].append({
                        "recommendationId": check.get('recommendation_id', ''),
                        "description": check.get('description', ''),
                        "category": check.get('category', ''),
                        "impact": check.get('impact', ''),
                        "type": evaluation.get('resource_type', ''),
                        "id": resource_id,
                        "name": evaluation.get('resource_name', ''),
                        "resourceGroup": resource_id.split('/resourceGroups/')[1].split('/')[0] if '/resourceGroups/' in resource_id else '',
                        "potentialBenefits": check.get('potential_benefits', ''),
                        "longDescription": check.get('long_description', ''),
                        "learnMoreLink": check.get('learn_more_links', [])
                    })
        
        # Save WARA-compatible format
        wara_path = get_subscription_dir(args.subscription_id) / "aprl_recommendations.json"
        with open(wara_path, 'w') as f:
            json.dump(wara_format, f, indent=2)

        # Save detailed evaluation log with executed queries and responses
        detailed_path = get_subscription_dir(args.subscription_id) / "resilience_evaluations_detailed.json"
        detailed_payload = {
            "subscription_id": args.subscription_id,
            "recommendation_runs": detail_log,
        }
        with open(detailed_path, "w") as f:
            json.dump(detailed_payload, f, indent=2)
        
        LOGGER.info(f"✓ WARA-compatible format saved: {wara_path}")

        # Print summary
        total_checks = sum(e.get("total_checks", 0) for e in evaluations.values())
        total_failed = sum(e.get("failed_checks", 0) for e in evaluations.values())
        total_passed = sum(e.get("passed_checks", 0) for e in evaluations.values())
        LOGGER.info(
            "✓ Resilience evaluation complete: %d resources, %d total checks (%d passed, %d failed)",
            len(evaluations), total_checks, total_passed, total_failed
        )
        return 0

    except Exception as e:
        LOGGER.error("Error during evaluation: %s", e, exc_info=True)
        return 1


if __name__ == "__main__":
    exit(main())
