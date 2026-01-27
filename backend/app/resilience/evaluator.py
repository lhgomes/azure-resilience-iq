"""
Resiliency evaluation engine - evaluates workload components against APRL recommendations.

This module evaluates Azure resources against Azure Proactive Resiliency Library (APRL) v2
best practices. It determines which recommendations are met and which are not, organizing
results by category (Availability, Disaster Recovery, etc.).

Flow:
1. Load workload components (from discovery/LLM step)
2. Load APRL rules for each resource type
3. For each rule: evaluate if components pass the check
4. Organize results by category
5. Output evaluation results for scoring
"""

import os
import yaml
from datetime import datetime, timezone
from time import perf_counter
from typing import Dict, Any, List, Optional
from pathlib import Path

from app.models import WorkloadComponent


class ResiliencyEvaluator:
    """
    Evaluates workload components against APRL recommendations.
    
    Attributes:
        aprl_root: Path to APRL v2 repository root
        rules_dir: Path to generated resiliency rules directory
        subscription_filter: Optional subscription ID to filter resources
        resource_group_filter: Optional list of resource group names to filter
    """
    
    def __init__(
        self,
        aprl_root: str,
        rules_dir: str,
        subscription_filter: Optional[str] = None,
        resource_group_filter: Optional[List[str]] = None,
    ):
        """Initialize the resilience evaluator."""
        self.aprl_root = aprl_root
        self.rules_dir = rules_dir
        self.subscription_filter = subscription_filter.lower() if subscription_filter else None
        self.resource_group_filter = (
            [rg.lower() for rg in resource_group_filter] if resource_group_filter else None
        )
    
    def evaluate_workload(
        self,
        workload_name: str,
        components: List[WorkloadComponent],
    ) -> Dict[str, Any]:
        """
        Evaluate all components in the workload against APRL recommendations.
        
        Args:
            workload_name: Human-friendly name for the workload
            components: List of WorkloadComponent objects from discovery/LLM
            
        Returns:
            Dictionary containing evaluation results organized by component and category
        """
        start_time = perf_counter()
        
        # Group components by resource type
        components_by_type: Dict[str, List[WorkloadComponent]] = {}
        for comp in components:
            comp_type = comp.resource_type
            components_by_type.setdefault(comp_type, []).append(comp)
        
        all_results: List[Dict[str, Any]] = []
        
        # Evaluate each resource type
        for comp_type, comps_of_type in components_by_type.items():
            type_start = perf_counter()
            print(f"Evaluating resource type: {comp_type} ({len(comps_of_type)} components)")
            
            type_results = self._evaluate_components_of_type(comp_type, comps_of_type)
            all_results.extend(type_results)
            
            elapsed_type = perf_counter() - type_start
            print(f"Completed type {comp_type} in {elapsed_type:.3f}s")
        
        # Build final output
        output = {
            "workload_name": workload_name,
            "evaluation_timestamp": datetime.now(timezone.utc).isoformat(),
            "components": all_results,
        }
        
        total_elapsed = perf_counter() - start_time
        print(f"Total evaluation time: {total_elapsed:.3f}s")
        
        return output
    
    def _evaluate_components_of_type(
        self,
        comp_type: str,
        components: List[WorkloadComponent],
    ) -> List[Dict[str, Any]]:
        """
        Evaluate all components of a specific resource type.
        
        Args:
            comp_type: Azure resource type (e.g., "Microsoft.Compute/virtualMachines")
            components: List of components of this type
            
        Returns:
            List of evaluation results for each component
        """
        # Initialize result structure for each component
        comp_results: Dict[str, Dict[str, Any]] = {}
        for comp in components:
            comp_results[comp.id] = {
                "id": comp.id,
                "name": comp.name,
                "type": comp_type,
                "categories": {},
            }
        
        # Load rules for this resource type
        rules_file_path = self._get_rules_file_for_type(comp_type)
        
        if not rules_file_path or not os.path.exists(rules_file_path):
            print(f"No rules found for {comp_type}")
            return list(comp_results.values())
        
        with open(rules_file_path, "r", encoding="utf-8") as f:
            recommendations = yaml.safe_load(f) or []
        
        print(f"Loaded {len(recommendations)} recommendations for {comp_type}")
        
        # Evaluate each recommendation/rule
        for rec in recommendations:
            if not rec.get("automationAvailable", False):
                # Skip non-automatable recommendations
                continue
            
            aprl_guid = rec.get("aprlGuid")
            category = rec.get("category", "uncategorized")
            rec_id = rec.get("id", aprl_guid)
            
            if not aprl_guid:
                continue
            
            # Evaluate this rule against all components
            # This is where you'd integrate with APRL logic, KQL queries, or other evaluators
            evaluation_result = self._evaluate_recommendation(
                comp_type=comp_type,
                aprl_guid=aprl_guid,
                components=components,
            )
            
            # Organize results by component and category
            for comp_id, passed in evaluation_result.items():
                if comp_id not in comp_results:
                    continue
                
                if category not in comp_results[comp_id]["categories"]:
                    comp_results[comp_id]["categories"][category] = {
                        "passed": [],
                        "failed": [],
                    }
                
                if passed:
                    comp_results[comp_id]["categories"][category]["passed"].append(rec_id)
                else:
                    comp_results[comp_id]["categories"][category]["failed"].append(rec_id)
        
        return list(comp_results.values())
    
    def _evaluate_recommendation(
        self,
        comp_type: str,
        aprl_guid: str,
        components: List[WorkloadComponent],
    ) -> Dict[str, bool]:
        """
        Evaluate a single APRL recommendation against multiple components.
        
        This is the integration point where you would:
        - Load KQL queries from APRL
        - Execute them against Azure
        - Map results to components
        - Apply business logic
        
        Args:
            comp_type: Resource type
            aprl_guid: APRL recommendation GUID
            components: Components to evaluate
            
        Returns:
            Dictionary mapping component ID to pass/fail boolean
        """
        result = {}
        
        # Try to load KQL query from APRL
        kql_query = self._load_kql_for_recommendation(comp_type, aprl_guid)
        
        # For now, implement a simple heuristic-based evaluation
        # In production, execute KQL query against Azure and map results
        for comp in components:
            # Example: evaluate based on component properties
            passed = self._evaluate_component_by_heuristic(comp, aprl_guid)
            result[comp.id] = passed
        
        return result
    
    def _evaluate_component_by_heuristic(
        self,
        component: WorkloadComponent,
        aprl_guid: str,
    ) -> bool:
        """
        Simple heuristic-based evaluation.
        
        In production, this would execute KQL queries or call Azure APIs.
        For now, we use component properties as a proxy.
        
        Args:
            component: Component to evaluate
            aprl_guid: APRL recommendation GUID
            
        Returns:
            True if component passes the recommendation, False otherwise
        """
        # Example heuristic logic based on component properties
        # In production: execute KQL against Azure Monitor, ARG, etc.
        
        properties = component.properties or {}
        
        # Mock evaluation: check if component has resilience-related properties
        if aprl_guid.startswith("ha-"):  # High availability checks
            return properties.get("has_redundancy", False)
        
        if aprl_guid.startswith("dr-"):  # Disaster recovery checks
            return properties.get("has_backup", False)
        
        if aprl_guid.startswith("monitoring-"):  # Monitoring checks
            return properties.get("monitoring_enabled", False)
        
        # Default: assume not passing unless explicitly configured
        return False
    
    def _get_rules_file_for_type(self, resource_type: str) -> Optional[str]:
        """
        Get the rules file path for a resource type.
        
        Args:
            resource_type: Azure resource type
            
        Returns:
            Path to rules YAML file, or None if not found
        """
        # Extract resource name from type (e.g., "virtualMachines" from "Microsoft.Compute/virtualMachines")
        segment = resource_type.split("/")[-1].lower()
        rules_path = os.path.join(self.rules_dir, f"{segment}.yaml")
        return rules_path
    
    def _load_kql_for_recommendation(
        self,
        resource_type: str,
        aprl_guid: str,
    ) -> Optional[str]:
        """
        Load KQL query for a recommendation from APRL.
        
        Args:
            resource_type: Azure resource type
            aprl_guid: APRL recommendation GUID
            
        Returns:
            KQL query string, or None if not found
        """
        # Try to find and load KQL query from APRL directory structure
        folder_name = resource_type.split("/")[-1]
        
        for root, dirs, files in os.walk(self.aprl_root):
            if os.path.basename(root).lower() == folder_name.lower():
                candidate = os.path.join(root, "kql", f"{aprl_guid}.kql")
                if os.path.exists(candidate):
                    try:
                        with open(candidate, "r", encoding="utf-8") as f:
                            return f.read()
                    except Exception as e:
                        print(f"Error reading KQL file {candidate}: {e}")
        
        return None
