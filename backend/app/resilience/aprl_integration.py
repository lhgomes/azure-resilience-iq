"""
APRL v2 Integration Module

Provides direct access to APRL v2 recommendations, rules, and evaluators.
APRL v2 is structured as a curated catalog of YAML-based recommendations
organized by Azure resource type and resilience category.

Structure:
  backend/aprl/azure-resources/{ServiceType}/{ResourceType}/
    ├── recommendations.yaml  (Pre-defined rules with metadata)
    ├── kql/                  (Azure Resource Graph queries)
    └── _index.md             (Documentation)
"""

import os
import yaml
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum

from azure.core.exceptions import HttpResponseError, ServiceResponseError
from azure.mgmt.resourcegraph.models import QueryRequest

from app.collector.auth import get_arg_client
from app.resilience.heuristic_validator import HeuristicValidator, ValidationStrategy

LOGGER = logging.getLogger(__name__)


class ResiliencyCategory(str, Enum):
    """APRL resilience categories."""
    HIGH_AVAILABILITY = "HighAvailability"
    DISASTER_RECOVERY = "DisasterRecovery"
    MONITORING = "MonitoringandAlerting"
    SECURITY = "Security"


@dataclass
class APRLRecommendation:
    """Single APRL recommendation."""
    guid: str
    description: str
    category: str
    impact: str
    resource_type: str
    long_description: str
    potential_benefits: str
    automation_available: bool
    learn_more_links: List[Dict[str, str]]
    aprl_guid: str = ""  # Internal APRL GUID (used for KQL file lookups)
    recommendation_type_id: str = ""  # External ID (WARA format)
    recommendations_file_path: str = ""  # Path to recommendations.yaml for KQL lookup
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "guid": self.guid,
            "description": self.description,
            "category": self.category,
            "impact": self.impact,
            "resource_type": self.resource_type,
            "long_description": self.long_description,
            "potential_benefits": self.potential_benefits,
            "automation_available": self.automation_available,
            "learn_more_links": self.learn_more_links,
        }


@dataclass
class APRLEvaluationResult:
    """Result of evaluating a resource against APRL rules."""
    resource_id: str
    resource_type: str
    resource_name: str
    checks: List[Dict[str, Any]]  # List of all checks performed with status (pass/fail)
    scores: Dict[str, float]  # Scores per category
    overall_score: float


class APRLCatalog:
    """
    Direct consumer of APRL v2 recommendations.
    
    Loads all APRL recommendations from YAML files and provides
    programmatic access to rules by resource type and category.
    """
    
    def __init__(self, aprl_root: str):
        """
        Initialize APRL catalog.
        
        Args:
            aprl_root: Path to APRL v2 repository root (e.g., './backend/aprl')
        """
        self.aprl_root = Path(aprl_root)
        self.recommendations: Dict[str, List[APRLRecommendation]] = {}
        self.resource_type_index: Dict[str, str] = {}  # resource_type -> file_path
        self._load_all_recommendations()

    @staticmethod
    def _normalize_resource_type(resource_type: str) -> str:
        """Normalize resource type for consistent lookups."""
        return resource_type.lower()
    
    def _load_all_recommendations(self) -> None:
        """Load all YAML recommendation files from APRL structure."""
        # Resolve to absolute path
        aprl_path = self.aprl_root.resolve() if isinstance(self.aprl_root, Path) else Path(self.aprl_root).resolve()
        
        # Search all Azure resource types
        azure_resources = aprl_path / "azure-resources"
        if not azure_resources.exists():
            LOGGER.warning(f"APRL azure-resources directory not found: {azure_resources}")
            return
        
        for rec_file in azure_resources.rglob("recommendations.yaml"):
            try:
                self._load_recommendation_file(str(rec_file))
            except Exception as e:
                LOGGER.error(f"Failed to load {rec_file}: {e}")
    
    def _load_recommendation_file(self, file_path: str) -> None:
        """Load recommendations from a single YAML file."""
        with open(file_path, 'r') as f:
            data = yaml.safe_load(f)
            if not data:
                return
        
        for rec_dict in data:
            try:
                # Parse YAML into APRLRecommendation object
                # Keep both IDs: recommendationTypeId (WARA) for output, aprlGuid for KQL filenames
                recommendation_type_id = rec_dict.get("recommendationTypeId", "")
                aprl_guid = rec_dict.get("aprlGuid", "")
                guid = recommendation_type_id or aprl_guid
                
                recommendation = APRLRecommendation(
                    guid=guid,
                    recommendation_type_id=recommendation_type_id,
                    aprl_guid=aprl_guid,
                    recommendations_file_path=file_path,
                    description=rec_dict.get("description", ""),
                    category=rec_dict.get("recommendationControl", ""),
                    impact=rec_dict.get("recommendationImpact", ""),
                    resource_type=rec_dict.get("recommendationResourceType", ""),
                    long_description=rec_dict.get("longDescription", ""),
                    potential_benefits=rec_dict.get("potentialBenefits", ""),
                    automation_available=rec_dict.get("automationAvailable", False),
                    learn_more_links=rec_dict.get("learnMoreLink", []),
                )
                
                # Index by resource type
                resource_type = recommendation.resource_type
                resource_type_key = self._normalize_resource_type(resource_type)
                if resource_type_key not in self.recommendations:
                    self.recommendations[resource_type_key] = []
                
                self.recommendations[resource_type_key].append(recommendation)
                self.resource_type_index[resource_type_key] = file_path
                
            except Exception as e:
                LOGGER.error(f"Failed to parse recommendation in {file_path}: {e}")
    
    def get_recommendations_by_resource_type(
        self, 
        resource_type: str,
        category: Optional[str] = None
    ) -> List[APRLRecommendation]:
        """
        Get all recommendations for a resource type.
        
        Args:
            resource_type: Azure resource type (e.g., 'Microsoft.Compute/virtualMachines')
            category: Optional category filter (HighAvailability, DisasterRecovery, etc.)
        
        Returns:
            List of matching recommendations
        """
        resource_type_key = self._normalize_resource_type(resource_type)
        recs = self.recommendations.get(resource_type_key, [])
        
        if category:
            recs = [r for r in recs if r.category == category]
        
        return recs
    
    def get_all_categories(self, resource_type: str) -> List[str]:
        """Get unique resilience categories for a resource type."""
        resource_type_key = self._normalize_resource_type(resource_type)
        recs = self.recommendations.get(resource_type_key, [])
        return list(set(r.category for r in recs))
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics about loaded recommendations."""
        total_recs = sum(len(recs) for recs in self.recommendations.values())
        categories = set()
        
        for recs in self.recommendations.values():
            for rec in recs:
                categories.add(rec.category)
        
        return {
            "total_recommendations": total_recs,
            "resource_types_covered": len(self.recommendations),
            "categories": list(categories),
            "resource_types": list(self.recommendations.keys()),
        }


class APRLEvaluator:
    """
    APRL-based resilience evaluator.
    
    Takes workload resources and evaluates them against APRL recommendations
    to identify resilience gaps and provide recommendations.
    """

    @staticmethod
    def _normalize_category(name: str) -> str:
        """Normalize category names for matching (case/spacing agnostic)."""
        return "".join(ch for ch in name if ch.isalnum()).lower()
    
    def __init__(self, catalog: APRLCatalog, category_weights: Dict[str, float], aoai_client: Optional[object] = None):
        """
        Initialize evaluator.
        
        Args:
            catalog: Loaded APRLCatalog
            category_weights: Weights for each resilience category
            aoai_client: Optional Azure OpenAI client for LLM strategy generation
        """
        self.catalog = catalog
        self.aoai_client = aoai_client
        self.category_weights = {
            self._normalize_category(cat): weight for cat, weight in category_weights.items()
        }

    @staticmethod
    def _inject_resource_filter(kql_query: str, resource_ids: List[str]) -> str:
        """Append id filter to the KQL so it runs once per resource type."""
        if not resource_ids:
            return kql_query
        id_list = ", ".join(f"'{rid}'" for rid in resource_ids)

        lines = kql_query.rstrip().splitlines()
        filter_line = f"| where id in~ ({id_list})"

        inserted = False
        new_lines: List[str] = []
        for line in lines:
            new_lines.append(line)
            # Insert right after the first Resources line to ensure id is in scope
            if not inserted and line.strip().lower().startswith("resources"):
                new_lines.append(filter_line)
                inserted = True

        if not inserted:
            new_lines.append(filter_line)

        return "\n".join(new_lines) + "\n"

    def _execute_kql(
        self,
        subscription_id: str,
        kql_query: str,
        max_retries: int = 3,
        base_sleep: float = 1.0,
    ) -> List[Dict[str, Any]]:
        """Run a KQL query against Azure Resource Graph with basic retries."""
        LOGGER.debug("Executing KQL for subscription %s:\n%s", subscription_id, kql_query)
        client = get_arg_client()
        request = QueryRequest(subscriptions=[subscription_id], query=kql_query)

        retryable_status = {408, 429, 500, 502, 503, 504}

        for attempt in range(1, max_retries + 1):
            try:
                response = client.resources(request)
                return list(response.data or [])
            except ServiceResponseError as exc:
                if attempt >= max_retries:
                    raise
                sleep_for = base_sleep * attempt
                LOGGER.warning(
                    "Transient KQL error (attempt %s/%s): %s", attempt, max_retries, exc
                )
                time.sleep(sleep_for)
            except HttpResponseError as exc:
                status = getattr(exc, "status_code", None)
                if status in retryable_status and attempt < max_retries:
                    sleep_for = base_sleep * attempt
                    LOGGER.warning(
                        "Retryable HTTP %s for KQL (attempt %s/%s): %s",
                        status,
                        attempt,
                        max_retries,
                        exc,
                    )
                    time.sleep(sleep_for)
                    continue
                raise

    def _check_property_based_finding(
        self,
        resource_type: str,
        aprl_guid: str,
        description: str,
        long_description: str,
        potential_benefits: str,
        resource: Dict[str, Any],
        impact: str = "Medium",
    ) -> Tuple[bool, Optional[ValidationStrategy]]:
        """
        Check if a resource likely fails a recommendation using heuristic or LLM analysis.
        
        When KQL is unavailable, analyzes recommendation metadata to infer
        what properties to check and validates resource against those properties.
        
        Strategy selection:
        1. First tries heuristic pattern matching (fast, no cost)
        2. If heuristic not confident enough and LLM is available, tries LLM analysis
        3. Returns strategy if confidence >= 0.5, otherwise returns None
        4. ESCALATION: For high impact, failures, or low confidence, uses full LLM analysis
        
        Returns:
            (is_failing, strategy_used): 
            - is_failing=True if resource likely FAILS the recommendation
            - strategy_used: The ValidationStrategy that was applied, or None
        """
        validator = HeuristicValidator(aoai_client=self.aoai_client)
        
        # Analyze recommendation to build validation strategy
        # If LLM is available, it will be used as fallback if heuristic confidence is low
        strategy = validator.analyze_recommendation(
            recommendation_id=aprl_guid,
            aprl_guid=aprl_guid,
            resource_type=resource_type,
            description=description,
            long_description=long_description,
            potential_benefits=potential_benefits,
            use_llm=(self.aoai_client is not None),  # Enable LLM if client available
        )
        
        if not strategy:
            # No strategy could be inferred
            LOGGER.debug(
                f"No heuristic or LLM strategy for {aprl_guid} on {resource_type}"
            )
            return False, None
        
        # Check confidence level
        if strategy.confidence < 0.5:
            LOGGER.debug(
                f"Strategy confidence too low ({strategy.confidence:.1%}) "
                f"for {aprl_guid} ({strategy.strategy_type}): {strategy.logic}"
            )
            return False, strategy
        
        # Log strategy type used
        strategy_type_str = strategy.strategy_type.upper()
        LOGGER.debug(
            f"Using {strategy_type_str} strategy (confidence: {strategy.confidence:.0%}) "
            f"for {aprl_guid}"
        )
        
        # Apply strategy to this specific resource
        is_failing, reason = validator.apply_strategy(strategy, resource)
        
        if is_failing:
            LOGGER.debug(
                f"Strategy validation flagged resource {resource.get('id')}: {reason}"
            )
        
        # INTELLIGENT ESCALATION: Use full LLM analysis for critical cases
        # Triggers when:
        # 1. High impact recommendation (critical/important)
        # 2. Property checks failed (need detailed reasoning)
        # 3. Very low confidence (<40%)
        should_escalate = False
        escalation_reason = None
        
        if self.aoai_client:
            if impact and impact.lower() in ['high', 'critical']:
                should_escalate = True
                escalation_reason = "high impact recommendation"
            elif is_failing and strategy.strategy_type in ['heuristic', 'llm']:
                should_escalate = True
                escalation_reason = "property checks failed"
            elif strategy.confidence < 0.4:
                should_escalate = True
                escalation_reason = f"low confidence ({strategy.confidence:.0%})"
        
        if should_escalate:
            LOGGER.info(
                f"🔍 Escalating to full LLM analysis for {aprl_guid}: {escalation_reason}"
            )
            llm_fails, llm_reasoning = validator._llm_full_resource_analysis(
                recommendation_id=aprl_guid,
                aprl_guid=aprl_guid,
                resource_type=resource_type,
                description=description,
                long_description=long_description,
                impact=impact,
                resource=resource,
            )
            
            # If LLM analysis succeeded, use its result
            if llm_reasoning is not None:
                # Store LLM reasoning in strategy for logging
                if not hasattr(strategy, 'llm_reasoning'):
                    strategy.llm_reasoning = llm_reasoning
                    strategy.llm_analysis_used = True
                    strategy.strategy_type = "llm"  # Mark as LLM-based for validation_source
                is_failing = llm_fails
                LOGGER.info(
                    f"✓ LLM full analysis: {'FAILS' if llm_fails else 'PASSES'} - {llm_reasoning[:100]}"
                )
        
        return is_failing, strategy

    def _load_kql_for_recommendation(self, resource_type: str, aprl_guid: str, recommendations_file_path: str = "") -> Tuple[Optional[str], Optional[Path]]:
        """
        Load KQL file for a recommendation using the catalog index.
        
        Args:
            resource_type: Azure resource type
            aprl_guid: APRL GUID (internal, used for KQL filenames)
            recommendations_file_path: Path to recommendations.yaml (preferred for KQL lookup)
            
        Returns:
            Tuple of (KQL query content if found else None, Path of KQL file if found else None)
        """
        # Try to use the recommendations file path first (for cross-resource recommendations)
        kql_path = None
        if recommendations_file_path:
            kql_dir = Path(recommendations_file_path).parent / "kql"
            kql_path = kql_dir / f"{aprl_guid}.kql"
            if kql_path.exists():
                try:
                    LOGGER.debug(f"✓ Found KQL for {resource_type}: {aprl_guid}")
                    kql_text = kql_path.read_text(encoding="utf-8")
                    marker = kql_text.strip().lower()
                    if not marker:
                        LOGGER.info(
                            "Skipping empty KQL for %s (aprlGuid=%s)",
                            resource_type,
                            aprl_guid,
                        )
                        return None, kql_path
                    if marker.startswith("//") and (
                        "cannot-be-validated-with-arg" in marker
                        or "under-development" in marker
                    ):
                        LOGGER.info(
                            "Skipping placeholder KQL for %s (aprlGuid=%s)",
                            resource_type,
                            aprl_guid,
                        )
                        return None, kql_path

                    return kql_text, kql_path
                except Exception as exc:
                    LOGGER.warning(f"Failed to read KQL file {kql_path}: {exc}")
                    # Fall through to resource-type-based lookup
        
        # Fallback: look up by resource type
        normalized_type = self.catalog._normalize_resource_type(resource_type)
        rec_file = self.catalog.resource_type_index.get(normalized_type)
        if not rec_file:
            LOGGER.debug(f"No recommendation file found for resource type: {resource_type}")
            return None, None
        
        kql_dir = Path(rec_file).parent / "kql"
        
        if not aprl_guid:
            LOGGER.debug(f"No aprlGuid provided for {resource_type}")
            return None, None

        kql_path = kql_dir / f"{aprl_guid}.kql"
        if kql_path.exists():
            try:
                LOGGER.debug(f"✓ Found KQL for {resource_type}: {aprl_guid}")
                kql_text = kql_path.read_text(encoding="utf-8")
                marker = kql_text.strip().lower()
                if not marker:
                    LOGGER.info(
                        "Skipping empty KQL for %s (aprlGuid=%s)",
                        resource_type,
                        aprl_guid,
                    )
                    return None, kql_path
                if marker.startswith("//") and (
                    "cannot-be-validated-with-arg" in marker
                    or "under-development" in marker
                ):
                    LOGGER.info(
                        "Skipping placeholder KQL for %s (aprlGuid=%s)",
                        resource_type,
                        aprl_guid,
                    )
                    return None, kql_path

                return kql_text, kql_path
            except Exception as exc:
                LOGGER.warning(f"Failed to read KQL file {kql_path}: {exc}")
                return None, kql_path

        LOGGER.warning(
            f"KQL file not found for {resource_type} with aprlGuid {aprl_guid}. "
            f"Checked: {kql_dir / aprl_guid}.kql"
        )
        return None, kql_path

    
    def evaluate_resource(
        self,
        resource_id: str,
        resource_name: str,
        resource_type: str,
        criticality_weight: float = 1.0
    ) -> APRLEvaluationResult:
        """
        Evaluate a single resource against APRL recommendations.
        
        Args:
            resource_id: Azure resource ID
            resource_name: Display name
            resource_type: Azure resource type
            criticality_weight: Criticality weight (0-100)
        
        Returns:
            Evaluation result with findings and scores
        """
        # Get all APRL recommendations for this resource type
        recommendations = self.catalog.get_recommendations_by_resource_type(resource_type)
        
        if not recommendations:
            return APRLEvaluationResult(
                resource_id=resource_id,
                resource_type=resource_type,
                resource_name=resource_name,
                checks=[],
                scores={cat: 100.0 for cat in self.category_weights.keys()},
                overall_score=100.0,
            )
        
        # Group recommendations by category
        by_category = {}
        for rec in recommendations:
            if rec.category not in by_category:
                by_category[rec.category] = []
            by_category[rec.category].append(rec)
        
        # Evaluate per category (in real scenario, would check actual resource config)
        category_scores = {}
        all_findings = []
        
        for category, recs in by_category.items():
            # Simple scoring: assume all recommendations as potential findings
            # In real implementation, would check actual resource properties
            
            # Placeholder: assume 50% compliance rate for demo
            compliance_rate = 0.5
            category_score = compliance_rate * 100
            category_scores[category] = category_score
            
            # Create findings for each recommendation
            for rec in recs:
                all_findings.append({
                    "recommendation_id": rec.guid,
                    "description": rec.description,
                    "category": rec.category,
                    "impact": rec.impact,
                    "long_description": rec.long_description,
                    "potential_benefits": rec.potential_benefits,
                    "learn_more": rec.learn_more_links,
                })
        
        # Calculate weighted overall score
        overall_score = self._calculate_weighted_score(category_scores)
        
        # Apply criticality weight to findings
        for finding in all_findings:
            finding["criticality_weight"] = criticality_weight
        
        return APRLEvaluationResult(
            resource_id=resource_id,
            resource_type=resource_type,
            resource_name=resource_name,
            checks=all_findings,
            scores=category_scores,
            overall_score=overall_score,
        )

    def evaluate_resources_batch(
        self,
        resource_type: str,
        resources: List[Dict[str, Any]],
        criticality_weights: Dict[str, float],
        subscription_id: str,
        detail_log: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, APRLEvaluationResult]:
        """Evaluate all resources of a type with one KQL per recommendation, batch pending guidance."""

        results: Dict[str, APRLEvaluationResult] = {}
        resource_ids = [r.get("id") for r in resources if r.get("id")]
        # Normalize IDs to lower-case for consistent matching with ARG results
        resource_ids_lower = [rid.lower() for rid in resource_ids]

        recommendations = self.catalog.get_recommendations_by_resource_type(resource_type)

        # If APRL has no rules for this type, return perfect scores
        if not recommendations:
            for res in resources:
                rid = res.get("id")
                results[rid] = APRLEvaluationResult(
                    resource_id=rid,
                    resource_type=resource_type,
                    resource_name=res.get("name", "Unknown"),
                    checks=[],
                    scores={cat: 100.0 for cat in self.category_weights.keys()},
                    overall_score=100.0,
                )
            return results

        # Totals per category (same for all resources of this type)
        category_totals: Dict[str, int] = {}
        display_categories: Dict[str, str] = {}
        for rec in recommendations:
            cat_norm = self._normalize_category(rec.category)
            category_totals[cat_norm] = category_totals.get(cat_norm, 0) + 1
            display_categories.setdefault(cat_norm, rec.category)

        # Per-resource tracking
        checks_map: Dict[str, List[Dict[str, Any]]] = {rid: [] for rid in resource_ids}
        category_fail_counts: Dict[str, Dict[str, int]] = {rid: {} for rid in resource_ids}
        
        # Collect items that need batch LLM guidance (no KQL, heuristic inconclusive)
        pending_items = []
        pending_map = {}  # (rid, rec.guid) -> index in checks_map[rid]
        strategy_map = {}  # (rid, rec.guid) -> (is_failing, strategy)

        # Execute each recommendation's KQL once, scoped to the resources of this type
        for rec in recommendations:
            # KQL files are named with the internal aprlGuid
            kql_query, kql_path = self._load_kql_for_recommendation(
                resource_type,
                rec.aprl_guid,
                rec.recommendations_file_path
            )
            failing_ids: set = set()

            detail_entry: Dict[str, Any] = {
                "resource_type": resource_type,
                "recommendation_id": rec.guid,
                "aprl_guid": rec.aprl_guid,
                "kql_path": str(kql_path) if kql_path else None,
                "query": None,
                "rows": None,
                "error": None,
                "status": None,
            }

            if kql_query:
                filtered_query = self._inject_resource_filter(kql_query, resource_ids_lower)
                detail_entry["query"] = filtered_query
                try:
                    rows = self._execute_kql(subscription_id, filtered_query)
                    failing_ids = {
                        row.get("id") for row in rows if isinstance(row, dict) and row.get("id")
                    }
                    detail_entry["rows"] = rows
                    detail_entry["status"] = "success"
                except HttpResponseError as exc:
                    LOGGER.error(
                        "KQL execution failed for resource_type=%s recommendation=%s: %s",
                        resource_type,
                        rec.guid,
                        exc,
                    )
                    failing_ids = set()
                    detail_entry["error"] = str(exc)
                    detail_entry["status"] = "error_http"
                except ServiceResponseError as exc:
                    LOGGER.error(
                        "Transient KQL error for resource_type=%s recommendation=%s: %s",
                        resource_type,
                        rec.guid,
                        exc,
                    )
                    failing_ids = set()
                    detail_entry["error"] = str(exc)
                    detail_entry["status"] = "error_transient"
            else:
                detail_entry["status"] = "missing_kql"
                # Try property-based fallback checks using heuristic validator with LLM escalation
                LOGGER.debug(
                    "No KQL for %s recommendation=%s; trying heuristic validation",
                    resource_type,
                    rec.guid,
                )
                for res in resources:
                    rid = res.get("id")
                    if rid:
                        is_failing, strategy = self._check_property_based_finding(
                            resource_type=resource_type,
                            aprl_guid=rec.aprl_guid,
                            description=rec.description,
                            long_description=rec.long_description,
                            potential_benefits=rec.potential_benefits,
                            resource=res,
                            impact=rec.impact,  # Pass impact for escalation logic
                        )
                        
                        # Store strategy for later processing
                        strategy_map[(rid, rec.guid)] = (is_failing, strategy)
                        
                        # If no strategy found or confidence too low, mark for pending review
                        if not strategy or strategy.confidence < 0.5:
                            # No viable validation strategy - needs manual review
                            idx = len(checks_map[rid])
                            pending_items.append({
                                "id": f"{rid}:{rec.guid}",
                                "description": rec.description,
                                "impact": rec.impact,
                                "long_description": rec.long_description,
                                "potential_benefits": rec.potential_benefits
                            })
                            pending_map[(rid, rec.guid)] = idx
                            checks_map[rid].append({
                                "recommendation_id": rec.guid,
                                "description": rec.description,
                                "category": rec.category,
                                "impact": rec.impact,
                                "long_description": rec.long_description,
                                "potential_benefits": rec.potential_benefits,
                                "learn_more": rec.learn_more_links,
                                "criticality_weight": criticality_weights.get(rid, 1.0),
                                "status": "pending",
                                "validation_source": "PendingReview"
                            })
                        elif strategy and strategy.confidence >= 0.5:
                            # Strategy has sufficient confidence - add as heuristic/LLM result
                            if is_failing:
                                failing_ids.add(rid)
                            if strategy:
                                detail_entry["heuristic_strategy"] = {
                                    "type": strategy.strategy_type,
                                    "logic": strategy.logic,
                                    "confidence": strategy.confidence,
                                }
                                # Include LLM reasoning if full analysis was used
                                if hasattr(strategy, 'llm_reasoning'):
                                    detail_entry["heuristic_strategy"]["llm_reasoning"] = strategy.llm_reasoning
                                    detail_entry["heuristic_strategy"]["llm_analysis_used"] = True
                
                if failing_ids:
                    detail_entry["status"] = "success_heuristic"
                    detail_entry["rows"] = [{"id": fid} for fid in failing_ids]
                else:
                    detail_entry["heuristic_analysis"] = "No applicable heuristics or pending review"

            # Consider presence in result set as a failing finding
            failing_ids_lower = {fid.lower() for fid in failing_ids if fid}

            # Add check result for ALL resources (both pass and fail)
            # Determine validation_source based on whether KQL was available or strategy used
            validation_source = "APRL" if kql_query else "Heuristic"
            
            for rid in resource_ids:
                if rid:
                    # Skip if this check was marked as pending (already added)
                    if (rid, rec.guid) in pending_map:
                        continue
                    
                    is_failed = rid.lower() in failing_ids_lower
                    # For non-KQL checks, retrieve the strategy to see if LLM escalation was used
                    source = validation_source
                    # Get original learn_more (APRL has 0 or 1 item, stored as list)
                    learn_more = rec.learn_more_links[0] if rec.learn_more_links else {}
                    
                    if not kql_query and is_failed:
                        # Check if LLM escalation was used by looking at stored strategy
                        _, strategy = strategy_map.get((rid, rec.guid), (False, None))
                        if strategy and hasattr(strategy, 'llm_analysis_used') and strategy.llm_analysis_used:
                            source = "LLM"
                            # Add LLM reasoning to the learn_more object if available
                            if hasattr(strategy, 'llm_reasoning') and strategy.llm_reasoning:
                                # Add llm_reasoning field to existing object
                                learn_more = dict(learn_more) if learn_more else {}
                                learn_more["llm_reasoning"] = strategy.llm_reasoning
                    
                    checks_map[rid].append({
                        "recommendation_id": rec.guid,
                        "description": rec.description,
                        "category": rec.category,
                        "impact": rec.impact,
                        "long_description": rec.long_description,
                        "potential_benefits": rec.potential_benefits,
                        "learn_more": learn_more,
                        "criticality_weight": criticality_weights.get(rid, 1.0),
                        "status": "fail" if is_failed else "pass",
                        "validation_source": source
                    })
                    if is_failed:
                        cat_norm = self._normalize_category(rec.category)
                        category_fail_counts[rid][cat_norm] = category_fail_counts[rid].get(cat_norm, 0) + 1

            if detail_log is not None:
                detail_log.append(detail_entry)

        # Batch call to generate guidance for all pending items
        if pending_items:
            validator = HeuristicValidator(aoai_client=self.aoai_client)
            guidance_map = validator.generate_batch_user_guidance(pending_items)
            # Apply guidance to checks
            for item in pending_items:
                rec_id = item["id"]
                rid, guid = rec_id.split(":", 1)
                idx = pending_map.get((rid, guid))
                if idx is not None:
                    guide = guidance_map.get(rec_id, {})
                    checks_map[rid][idx]["learn_more"] = [
                        {"header": guide.get("quick_header", "Manual review required"),
                         "guide": guide.get("practical_guide", "Consult Azure documentation.")}
                    ]

        # Build per-resource results
        for res in resources:
            rid = res.get("id")
            if not rid:
                continue

            cat_scores: Dict[str, float] = {}
            for cat_norm, total in category_totals.items():
                failed = category_fail_counts[rid].get(cat_norm, 0)
                score = 100.0 if total == 0 else round(100.0 * (1 - failed / total), 1)
                cat_scores[cat_norm] = score

            overall = self._calculate_weighted_score(cat_scores)

            # Restore display categories (best-effort) using original category strings where possible
            display_scores = {}
            for cat_norm, total in category_totals.items():
                display_label = display_categories.get(cat_norm, cat_norm)
                display_scores[display_label] = cat_scores.get(cat_norm, 100.0)

            results[rid] = APRLEvaluationResult(
                resource_id=rid,
                resource_type=resource_type,
                resource_name=res.get("name", "Unknown"),
                checks=checks_map[rid],
                scores=display_scores,
                overall_score=overall,
            )

        return results
    
    def _calculate_weighted_score(self, category_scores: Dict[str, float]) -> float:
        """Calculate weighted overall score based on category weights."""
        total = 0.0

        normalized_scores = {
            self._normalize_category(cat): score for cat, score in category_scores.items()
        }

        for category, weight in self.category_weights.items():
            score = normalized_scores.get(category, 0.0)
            total += score * weight

        return round(total, 2)


def load_aprl_catalog(settings) -> APRLCatalog:
    """
    Load APRL catalog from settings.
    
    Args:
        settings: AppSettings instance
    
    Returns:
        Initialized APRLCatalog
    """
    aprl_root = settings.get_aprl_root()
    catalog = APRLCatalog(aprl_root)
    
    summary = catalog.get_summary()
    LOGGER.debug(f"Loaded APRL catalog: {summary}")
    
    return catalog
