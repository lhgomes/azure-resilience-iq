"""
APRL Resilience Evaluator CLI runner.

Usage:
  python -m app.resilience.run --subscription-id ebb79bc0-aa86-44a7-8111-cabbe0c43993

This script:
1. Loads the resources from collector output (data/{subscription_id}/resources.json)
2. Runs APRL evaluator against all resources
3. Saves evaluation results to data/{subscription_id}/resilience_evaluations.json
4. Reports success/failures

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
from typing import Dict, Optional, List, Any
from pathlib import Path

from dotenv import load_dotenv

from app.config import get_resources_path, get_subscription_dir
from app.settings import get_settings, load_settings
from app.logger import setup_logging, get_logger
from app.resilience.aprl_integration import load_aprl_catalog, APRLEvaluator, generate_resilience_check_id
from app.storage.resilience_evaluations_store import save_resilience_evaluations
from app.storage.llm_annotations_store import load_llm_annotations
from app.resilience.zonal_analyzer import ZonalAnalyzer, ZonalResilienceSummary, ZonalData, DeploymentPattern
from app.resilience.resilience_correlator import ResourceCorrelator, ResilienceGroupType

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


def _build_zone_findings_map(
    resilience_evaluations: Dict[str, Any],
    zone_findings_map: Dict[str, ZonalData]
) -> None:
    """
    Build a map of resource IDs to ZonalData from APRL/custom KQL zone findings.
    
    This function extracts zone-related recommendations from resilience evaluations
    and converts them into ZonalData objects for resources that would otherwise
    be marked as "UNKNOWN".
    
    Supported zone-related rule IDs:
    - storage-zone-redundancy-001: Storage Accounts (param1=current SKU, param2=recommended)
    - keyvault-zone-redundancy-001: Key Vaults (param1=current SKU, param2=recommended)
    - synapse-zone-redundancy-001: Synapse workspaces
    - datafactory-zone-redundancy-001: Data Factories
    - containerinstance-zone-redundancy-001: Container Instances
    - Any APRL zone-related recommendations
    """
    zone_rule_keywords = [
        'zone-redundancy',
        'zone-resilience',
        'availability-zone',
        'cross-zone',
        'redundancy',
    ]
    
    recommendation_runs = resilience_evaluations.get("recommendation_runs", [])
    
    # Track how many zone findings we build
    findings_count = 0
    
    for run in recommendation_runs:
        # The structure doesn't directly have resource_id, we need to extract it from resource properties
        # For now, collect by resource_type and build mappings from rows
        
        rec_id = run.get("recommendation_id", "").lower()
        
        # Check if this is a zone-related recommendation
        is_zone_related = any(keyword in rec_id for keyword in zone_rule_keywords)
        
        if not is_zone_related:
            continue
        
        # Extract data from KQL result
        rows = run.get("rows", [])
        status = run.get("status", "")
        
        if status == "success" and rows:
            # Found actual zone configuration data
            for row in rows:
                # The row contains the actual resource data
                # For storage: param1 = current SKU, param2 = recommended
                param1 = str(row.get("param1", "")).lower() if row.get("param1") else ""
                param2 = str(row.get("param2", "")).lower() if row.get("param2") else ""
                resource_id = row.get("id", "")  # The resource ID from KQL result
                
                if not resource_id:
                    continue
                
                # Normalize resource ID to lowercase for matching (Azure is case-insensitive)
                normalized_id = resource_id.lower()
                
                # Determine if currently zone-redundant
                is_zone_redundant = (
                    "zrs" in param1 or 
                    "zone" in param1 or 
                    "redundant" in param1 or
                    "ra-grs" in param1
                )
                
                # Build ZonalData from KQL findings
                zone_data = ZonalData(
                    zones_used=[],  # KQL doesn't provide zone array directly
                    is_zone_redundant=is_zone_redundant,
                    zone_count=3 if is_zone_redundant else 1,
                    meets_3az_requirement=is_zone_redundant,
                    deployment_pattern=(
                        DeploymentPattern.ZONE_REDUNDANT if is_zone_redundant
                        else DeploymentPattern.SINGLE_ZONE
                    ),
                    recommendation=f"Current: {param1}. Recommended: {param2}" if param2 else f"Current: {param1}",
                )
                
                zone_findings_map[normalized_id] = zone_data
                findings_count += 1
                LOGGER.debug(f"Built zone data for {resource_id} from {rec_id}: {param1}")
    
    LOGGER.info(f"Built {findings_count} zone findings from KQL queries")


def _generate_zone_recommendation_checks(
    zonal_data_list: List[Dict[str, Any]]
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Generate APRL-formatted zone recommendation checks from zonal_data.
    
    Creates checks in APRL format (like resilience_evaluations.json) with:
    - category: "HighAvailability"
    - validation_source: "ZoneRecommendation"
    - Same structure as APRL checks (impact, long_description, potential_benefits, learn_more)
    
    Args:
        zonal_data_list: List of resources with zonal_data from analyze_and_save_zonal_resilience
        
    Returns:
        Dictionary mapping resource_id -> list of zone recommendation checks
    """
    from app.resilience.zone_recommendation_engine import ZoneRecommendationEngine
    
    # Load zone-irrelevant types from config (control plane, regional networking, global services)
    settings = get_settings()
    zone_irrelevant_types = settings.get_zone_irrelevant_types()
    
    engine = ZoneRecommendationEngine()
    recommendations_by_resource = {}
    
    for resource_item in zonal_data_list:
        resource_id = resource_item.get("resource_id")
        resource_type = resource_item.get("resource_type")
        zonal_data = resource_item.get("zonal_data", {})
        
        if not resource_id or not resource_type:
            continue
            
        # Skip resource types that don't meaningfully support zones (from config)
        if resource_type.lower() in zone_irrelevant_types:
            LOGGER.debug(f"Skipping zone recommendation for {resource_type} - not zone-relevant")
            continue
            
        # Get deployment pattern
        deployment_pattern_str = zonal_data.get("deployment_pattern", "unknown")
        pattern_map = {
            "single_zone": DeploymentPattern.SINGLE_ZONE,
            "multi_zone_2": DeploymentPattern.MULTI_ZONE,
            "multi_zone_3plus": DeploymentPattern.MULTI_ZONE,
            "zone_redundant": DeploymentPattern.ZONE_REDUNDANT,
            "not_applicable": DeploymentPattern.NOT_APPLICABLE,
            "unknown": DeploymentPattern.UNKNOWN,
        }
        deployment_pattern = pattern_map.get(deployment_pattern_str, DeploymentPattern.UNKNOWN)
        
        # Get recommendation from engine
        try:
            zone_rec = engine.get_recommendation(
                resource_type=resource_type,
                deployment_pattern=deployment_pattern,
                zone_count=zonal_data.get("zone_count", 0),
                zones_used=zonal_data.get("zones_used", [])
            )
            
            # Determine status: fail if deployment_pattern suggests improvement needed
            status = "pass"
            if deployment_pattern in [DeploymentPattern.SINGLE_ZONE, DeploymentPattern.UNKNOWN]:
                # Single zone or unknown patterns usually need improvement
                status = "fail"
            elif deployment_pattern == DeploymentPattern.NOT_APPLICABLE:
                # Resource type doesn't support zones
                status = "pass"
            
            # Create APRL-formatted check
            check = {
                "recommendation_id": zone_rec.aprl_guid or f"zone-{resource_type.replace('/', '-')}-{deployment_pattern_str}",
                "description": zone_rec.description,
                "category": "HighAvailability",
                "impact": zone_rec.recommendation_impact,
                "long_description": zone_rec.long_description,
                "potential_benefits": zone_rec.potential_benefits,
                "learn_more": {
                    "name": "Learn More",
                    "links": zone_rec.learn_more_links
                } if zone_rec.learn_more_links else {},
                "status": status,
                "validation_source": ["ZoneRecommendation"],
                "deployment_pattern": deployment_pattern_str,
                "resilience_check_id": generate_resilience_check_id(resource_id, zone_rec.aprl_guid or f"zone-{resource_type.replace('/', '-')}-{deployment_pattern_str}"),
            }
            
            if resource_id not in recommendations_by_resource:
                recommendations_by_resource[resource_id] = []
            recommendations_by_resource[resource_id].append(check)
            
        except Exception as e:
            LOGGER.debug(f"Failed to generate zone recommendation for {resource_id}: {e}")
            continue
    
    return recommendations_by_resource


def analyze_and_save_zonal_resilience(
    subscription_id: str,
    resources: List[Dict[str, Any]],
    data_dir: Path,
    resilience_evaluations: Optional[Dict[str, Any]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Analyze zone configuration with resilience group correlation and save to zonal_resilience.json.
    
    This runs as part of the resilience evaluation process and generates
    zone configuration analysis for all resources, with context from resilience groups
    (Availability Sets, VMSS, Load Balancers, etc.).
    
    Returns zone recommendation checks to be injected into resilience_evaluations.json.
    
    Args:
        subscription_id: Azure subscription ID
        resources: List of Azure resources from collector
        data_dir: Directory to save output file
        resilience_evaluations: Optional detailed resilience evaluations with zone findings
        
    Returns:
        Dictionary mapping resource_id to list of zone recommendation checks (APRL format)
    """
    from datetime import datetime, timezone
    from app.storage.groups_store import NodeGroup, save_group, load_groups
    
    LOGGER.info(f"Analyzing zone configuration for {len(resources)} resources...")
    
    # Step 1: Identify resilience groups
    correlator = ResourceCorrelator(resources)
    resilience_groups = correlator.identify_groups()
    LOGGER.info(f"Identified {len(resilience_groups)} resilience groups")
    
    # Step 2: Save resilience groups to graph (as NodeGroup objects)
    for resilience_group in resilience_groups:
        node_group = NodeGroup(
            id=resilience_group.id,
            name=resilience_group.name,
            nodes=resilience_group.member_ids
        )
        try:
            save_group(subscription_id, node_group)
            LOGGER.debug(f"Created graph group: {resilience_group.name}")
        except Exception as e:
            LOGGER.warning(f"Failed to save group {resilience_group.id}: {e}")
    
    # Build a map of zone-related findings from resilience evaluations
    zone_findings_map = {}
    if resilience_evaluations:
        _build_zone_findings_map(resilience_evaluations, zone_findings_map)
    
    zonal_data_list = []
    
    for resource in resources:
        try:
            resource_id = resource.get("id")
            
            # Analyze with resilience group context
            zonal_data = ZonalAnalyzer.analyze_with_correlation(
                resource,
                correlator,
                resources
            )
            
            # If pattern is UNKNOWN, try to use APRL/custom KQL findings
            # Normalize ID for case-insensitive matching (Azure uses different cases)
            normalized_id = resource_id.lower() if resource_id else None
            if (zonal_data.deployment_pattern == DeploymentPattern.UNKNOWN and 
                normalized_id and normalized_id in zone_findings_map):
                zonal_data = zone_findings_map[normalized_id]
                LOGGER.debug(f"Enhanced zone data for {resource_id} using zone findings")
            
            # Get group membership info
            group = correlator.get_group_for_resource(resource_id)
            group_info = None
            if group:
                group_info = {
                    "group_id": group.id,
                    "group_type": group.type.value,
                    "group_name": group.name,
                    "member_count": len(group.member_ids)
                }
            
            zonal_data_list.append({
                "resource_id": resource.get("id"),
                "resource_name": resource.get("name"),
                "resource_type": resource.get("type"),
                "location": resource.get("location"),
                "resilience_group": group_info,
                "zonal_data": {
                    "zones_used": zonal_data.zones_used,
                    "is_zone_redundant": zonal_data.is_zone_redundant,
                    "zone_count": zonal_data.zone_count,
                    "meets_3az_requirement": zonal_data.meets_3az_requirement,
                    "deployment_pattern": zonal_data.deployment_pattern.value,
                }
            })
        except Exception as e:
            LOGGER.warning(f"Failed to analyze zones for {resource.get('id')}: {e}")
    
    # Calculate summary from all zonal data (pass resources for region analysis)
    # Extract the zonal_data objects that were already analyzed
    all_zonal = []
    for item in zonal_data_list:
        # Reconstruct ZonalData object from the dict representation
        zonal_dict = item["zonal_data"]
        zonal_data = ZonalData(
            zones_used=zonal_dict["zones_used"],
            is_zone_redundant=zonal_dict["is_zone_redundant"],
            zone_count=zonal_dict["zone_count"],
            meets_3az_requirement=zonal_dict["meets_3az_requirement"],
            deployment_pattern=DeploymentPattern(zonal_dict["deployment_pattern"]),
            recommendation=""  # Not needed for summary
        )
        all_zonal.append(zonal_data)
    
    results = {
        "subscription_id": subscription_id,
        "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
        "resilience_groups": {
            "count": len(resilience_groups),
            "by_type": {gt.value: len(correlator.get_groups_by_type(gt)) for gt in ResilienceGroupType}
        },
        "resources": zonal_data_list,
    }
    
    # Save to file
    output_file = data_dir / "zonal_resilience.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    # Generate zone recommendation checks for resilience_evaluations.json
    zone_recommendations_by_resource = {}
    try:
        zone_recommendations_by_resource = _generate_zone_recommendation_checks(zonal_data_list)
    except Exception as e:
        LOGGER.warning(f"Failed to generate zone recommendation checks: {e}")
    
    LOGGER.info(f"✓ Zonal analysis complete: {output_file}")
    LOGGER.info(f"  • Resilience groups: {len(resilience_groups)}")
    for gt in ResilienceGroupType:
        count = len(correlator.get_groups_by_type(gt))
        if count > 0:
            LOGGER.info(f"    - {gt.value}: {count}")
    # Log zone statistics from resources
    zone_redundant = sum(1 for z in all_zonal if z.deployment_pattern == DeploymentPattern.ZONE_REDUNDANT)
    multi_zone = sum(1 for z in all_zonal if z.deployment_pattern == DeploymentPattern.MULTI_ZONE)
    single_zone = sum(1 for z in all_zonal if z.deployment_pattern == DeploymentPattern.SINGLE_ZONE)
    compliant_3az = sum(1 for z in all_zonal if z.meets_3az_requirement)
    LOGGER.info(f"  • Zone-redundant: {zone_redundant}")
    LOGGER.info(f"  • Multi-zone: {multi_zone}")
    LOGGER.info(f"  • Single-zone: {single_zone}")
    LOGGER.info(f"  • 3-AZ compliant: {compliant_3az}/{len(all_zonal)}")
    
    return zone_recommendations_by_resource


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
            subscription_name = resources_data.get("subscription_name", "")
            subscription_virtual_flag = bool(resources_data.get("virtual_resources", False))
        else:
            # Fallback for direct list format
            resources = resources_data
            subscription_name = ""
            subscription_virtual_flag = False

        virtual_flags = [bool(res.get("virtual", False)) for res in resources]
        all_virtual_resources = bool(resources) and all(virtual_flags)
        any_virtual_resources = any(virtual_flags)

        # Detect if this is a virtual (non-Azure) subscription, e.g., Terraform input
        is_virtual_subscription = subscription_virtual_flag or all_virtual_resources or subscription_name == "Terraform"
        if is_virtual_subscription:
            LOGGER.info("Virtual resources input detected. Using LLM-based evaluation instead of KQL.")
        elif any_virtual_resources:
            LOGGER.info("Mixed virtual/non-virtual resources detected; KQL will target non-virtual resources only.")
        
        LOGGER.info("Loaded %d resources", len(resources))

        # Load LLM annotations
        LOGGER.debug("Loading LLM annotations...")

        # Create evaluator with optional LLM client
        evaluator = APRLEvaluator(catalog, aoai_client=aoai_client)

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
                subscription_id=args.subscription_id,
                detail_log=detail_log,
                is_virtual_subscription=is_virtual_subscription,
            )

            for resource_id, result in batch_results.items():
                evaluations[resource_id] = {
                    "resource_id": result.resource_id,
                    "resource_type": result.resource_type,
                    "resource_name": result.resource_name,
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
                subscription_id=args.subscription_id,
                detail_log=detail_log,
                is_virtual_subscription=is_virtual_subscription,
            )
            
            for resource_id, result in sub_results.items():
                evaluations[resource_id] = {
                    "resource_id": result.resource_id,
                    "resource_type": result.resource_type,
                    "resource_name": result.resource_name,
                    "checks": result.checks,
                }
            
            LOGGER.info(f"✓ Added {len(sub_results)} subscription-level evaluations")

        LOGGER.debug("Evaluation complete: %d resources evaluated", len(evaluations))

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
        # All calculations done client-side
        # ========================================
        # No scoring needed - frontend handles all calculations
        LOGGER.info("✓ Resilience evaluation complete - all calculations done client-side")

        # Run zonal resilience analysis with zone findings from evaluations
        try:
            subscription_dir = get_subscription_dir(args.subscription_id)
            # Load detailed evaluations to extract zone findings
            zone_findings = None
            if detailed_path.exists():
                try:
                    zone_findings = json.loads(detailed_path.read_text())
                except Exception as e:
                    LOGGER.debug(f"Could not load detailed evaluations for zone findings: {e}")
            
            zone_recommendations = analyze_and_save_zonal_resilience(
                args.subscription_id, 
                resources, 
                subscription_dir,
                resilience_evaluations=zone_findings
            )
            
            # Inject zone recommendations into evaluations (with deduplication)
            for resource_id, zone_checks in zone_recommendations.items():
                if resource_id in evaluations:
                    existing_checks = evaluations[resource_id]["checks"]
                    
                    # Deduplicate: merge zone checks with existing APRL/Heuristic/LLM checks
                    for zone_check in zone_checks:
                        zone_desc_lower = zone_check.get("description", "").lower()
                        zone_rec_id = zone_check.get("recommendation_id", "")
                        
                        # Check if any existing check covers the same topic (by recommendation_id or keywords)
                        merged = False
                        for existing_check in existing_checks:
                            existing_rec_id = existing_check.get("recommendation_id", "")
                            existing_desc_lower = existing_check.get("description", "").lower()
                            
                            # Option 1: Same recommendation_id (exact match)
                            if zone_rec_id and existing_rec_id == zone_rec_id:
                                # Merge validation sources
                                existing_sources = existing_check.get("validation_source", [])
                                if isinstance(existing_sources, str):
                                    existing_sources = [existing_sources]
                                if "ZoneRecommendation" not in existing_sources:
                                    existing_sources.append("ZoneRecommendation")
                                    existing_check["validation_source"] = existing_sources

                                # Enrich existing APRL/Heuristic check with zone recommendation details
                                existing_long = existing_check.get("long_description", "")
                                existing_benefits = existing_check.get("potential_benefits", "")
                                existing_learn_more = existing_check.get("learn_more") or {}

                                # Treat synthetic custom KQL checks as placeholders that should be replaced
                                placeholder_long = existing_long.lower().startswith("custom zone redundancy validation")
                                placeholder_benefits = existing_benefits.lower() in ["", "ensures zone redundancy for high availability"]
                                placeholder_learn_more = not existing_learn_more or existing_learn_more == {}

                                if placeholder_long and zone_check.get("long_description"):
                                    existing_check["long_description"] = zone_check.get("long_description")
                                elif not existing_long and zone_check.get("long_description"):
                                    existing_check["long_description"] = zone_check.get("long_description")

                                if placeholder_benefits and zone_check.get("potential_benefits"):
                                    existing_check["potential_benefits"] = zone_check.get("potential_benefits")
                                elif not existing_benefits and zone_check.get("potential_benefits"):
                                    existing_check["potential_benefits"] = zone_check.get("potential_benefits")

                                if placeholder_learn_more and zone_check.get("learn_more"):
                                    existing_check["learn_more"] = zone_check.get("learn_more")

                                # Align impact/category with zone recommendation when placeholder content was used
                                if placeholder_long or placeholder_benefits or placeholder_learn_more:
                                    if zone_check.get("impact"):
                                        existing_check["impact"] = zone_check["impact"]
                                    if zone_check.get("category"):
                                        existing_check["category"] = zone_check["category"]
                                    if zone_check.get("description") and existing_desc_lower.startswith("custom zone redundancy check"):
                                        existing_check["description"] = zone_check["description"]
                                merged = True
                                LOGGER.debug(f"Merged ZoneRecommendation sources for {resource_id}: {existing_rec_id}")
                                break
                            
                            # Option 2: Check if any existing APRL/Heuristic check covers this zone guidance
                            existing_sources = existing_check.get("validation_source", [])
                            if isinstance(existing_sources, str):
                                existing_sources = [existing_sources]
                            if existing_sources and existing_sources[0] in ["APRL", "Heuristic"]:
                                # Check for common zone-related keywords overlap
                                zone_keywords = {"zone", "vmss", "flex", "redundant", "zrs", "availability"}
                                zone_words_in_zone = {word for word in zone_keywords if word in zone_desc_lower}
                                zone_words_in_aprl = {word for word in zone_keywords if word in existing_desc_lower}
                                
                                # If both mention zones/redundancy and share key terms, consider it duplicate
                                if zone_words_in_zone and zone_words_in_aprl and len(zone_words_in_zone & zone_words_in_aprl) >= 1:
                                    merged = True
                                    LOGGER.debug(f"Skipping duplicate zone check '{zone_check['description'][:50]}...' - already covered by {existing_sources[0]} check '{existing_check['description'][:50]}'")
                                    break
                        
                        if not merged:
                            evaluations[resource_id]["checks"].append(zone_check)
                            LOGGER.debug(f"Added zone recommendation to {resource_id}: {zone_check['description'][:50]}...")
                else:
                    LOGGER.debug(f"Resource {resource_id} not in evaluations, skipping zone checks")
        except Exception as e:
            LOGGER.error(f"Zonal analysis failed: {e}", exc_info=True)
            # Don't fail the whole process if zonal analysis fails

        # NOW SAVE evaluations after zone recommendations have been injected
        LOGGER.debug("Saving evaluation results with zone recommendations...")
        save_resilience_evaluations(args.subscription_id, evaluations)

        # Print summary (calculate from checks)
        total_checks = 0
        total_failed = 0
        total_passed = 0
        total_pending = 0
        for e in evaluations.values():
            checks = e.get("checks", [])
            total_checks += len(checks)
            total_failed += sum(1 for c in checks if c.get("status") == "fail")
            total_passed += sum(1 for c in checks if c.get("status") == "pass")
            total_pending += sum(1 for c in checks if c.get("status") == "pending")
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
