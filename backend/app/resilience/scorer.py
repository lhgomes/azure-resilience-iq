"""
Resilience scoring engine - calculates resilience scores based on evaluation results.

This module computes:
1. Component-level scores: (passed_checks / total_checks) weighted by category
2. Workload-level score: weighted sum of component scores
3. Category breakdown scores

The scoring uses category weights to emphasize certain resilience areas.
"""

from typing import Dict, Any, List
import yaml
import os
from datetime import datetime, timezone


class ResilienceScorer:
    """
    Calculates resilience scores from evaluation results.
    
    Attributes:
        category_weights: Dictionary mapping category names to weights (should sum to 1.0)
        component_weights: Optional dictionary mapping component IDs to weights
    """
    
    def __init__(
        self,
        category_weights: Dict[str, float],
        component_weights: Dict[str, float] = None,
    ):
        """
        Initialize the scorer.
        
        Args:
            category_weights: Weights for each resilience category.
                            E.g., {"Availability": 0.4, "Recovery": 0.3, "Monitoring": 0.3}
            component_weights: Optional weights for individual components.
                             If provided, used for workload-level aggregation.
        """
        # Normalize weights to ensure they sum to 1.0
        total_weight = sum(category_weights.values())
        if total_weight == 0:
            raise ValueError("Category weights must sum to non-zero value")
        
        self.category_weights = {
            k: v / total_weight for k, v in category_weights.items()
        }
        
        self.component_weights = component_weights or {}
    
    def score_workload(
        self,
        evaluation_results: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Calculate resilience scores for the entire workload.
        
        Args:
            evaluation_results: Output from ResilienceEvaluator.evaluate_workload()
                              Contains components with pass/fail checks by category
            
        Returns:
            Dictionary with workload scores, component scores, and breakdown
        """
        components = evaluation_results.get("components", [])
        workload_name = evaluation_results.get("workload_name", "unknown")
        
        # Score each component
        component_scores = []
        
        for comp in components:
            comp_score = self._score_component(comp)
            component_scores.append(comp_score)
        
        # Aggregate component scores to workload level
        workload_score = self._aggregate_component_scores(component_scores)
        
        # Calculate category breakdown (average across components)
        category_breakdown = self._calculate_category_breakdown(components)
        
        # Build output
        output = {
            "workload_name": workload_name,
            "evaluation_timestamp": evaluation_results.get("evaluation_timestamp"),
            "scoring_timestamp": datetime.now(timezone.utc).isoformat(),
            "workload_score": workload_score,
            "category_scores": category_breakdown,
            "component_scores": component_scores,
            "summary": {
                "total_components": len(components),
                "components_evaluated": len([cs for cs in component_scores if cs.get("total_checks", 0) > 0]),
                "average_score": workload_score,  # For now, same as workload score
            }
        }
        
        return output
    
    def _score_component(self, component: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate resilience score for a single component.
        
        Score = sum(category_weight * category_score) for each category
        where category_score = passed_checks / total_checks
        
        Args:
            component: Component evaluation result with categories
            
        Returns:
            Component score with detailed breakdown
        """
        component_id = component.get("id")
        categories = component.get("categories", {})
        
        component_score = 0.0
        total_checks = 0
        total_passed = 0
        category_details = {}
        
        # Score each category
        for category_name, category_data in categories.items():
            passed = len(category_data.get("passed", []))
            failed = len(category_data.get("failed", []))
            total = passed + failed
            
            total_checks += total
            total_passed += passed
            
            if total == 0:
                raw_score = 0.0
            else:
                raw_score = passed / total
            
            # Apply category weight
            category_weight = self.category_weights.get(category_name, 0)
            component_score += raw_score * category_weight
            
            category_details[category_name] = {
                "score": raw_score,
                "passed": passed,
                "failed": failed,
                "total": total,
                "weight": category_weight,
            }
        
        return {
            "id": component_id,
            "name": component.get("name"),
            "type": component.get("type"),
            "score": component_score,
            "total_checks": total_checks,
            "total_passed": total_passed,
            "pass_rate": total_passed / total_checks if total_checks > 0 else 0.0,
            "categories": category_details,
            "weight": self.component_weights.get(component_id, 0),
        }
    
    def _aggregate_component_scores(self, component_scores: List[Dict[str, Any]]) -> float:
        """
        Aggregate component scores to workload level.
        
        If component weights are defined, uses weighted average.
        Otherwise, uses simple average.
        
        Args:
            component_scores: List of component score results
            
        Returns:
            Workload-level resilience score (0.0 to 1.0)
        """
        if not component_scores:
            return 0.0
        
        # Check if weights are available
        has_weights = any(cs.get("weight", 0) > 0 for cs in component_scores)
        
        if has_weights:
            # Weighted average
            total_weight = sum(cs.get("weight", 0) for cs in component_scores)
            if total_weight == 0:
                # Fallback to simple average if all weights are 0
                return sum(cs.get("score", 0) for cs in component_scores) / len(component_scores)
            
            weighted_sum = sum(
                cs.get("score", 0) * cs.get("weight", 0)
                for cs in component_scores
            )
            return weighted_sum / total_weight
        else:
            # Simple average
            return sum(cs.get("score", 0) for cs in component_scores) / len(component_scores)
    
    def _calculate_category_breakdown(self, components: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """
        Calculate average score for each resilience category across all components.
        
        Args:
            components: Component evaluation results
            
        Returns:
            Dictionary with category-level aggregate scores
        """
        category_totals: Dict[str, Dict[str, int]] = {}
        
        # Aggregate across all components
        for comp in components:
            categories = comp.get("categories", {})
            
            for cat_name, cat_data in categories.items():
                if cat_name not in category_totals:
                    category_totals[cat_name] = {
                        "passed": 0,
                        "failed": 0,
                    }
                
                category_totals[cat_name]["passed"] += len(cat_data.get("passed", []))
                category_totals[cat_name]["failed"] += len(cat_data.get("failed", []))
        
        # Calculate scores
        breakdown = {}
        for cat_name, totals in category_totals.items():
            total = totals["passed"] + totals["failed"]
            
            breakdown[cat_name] = {
                "score": totals["passed"] / total if total > 0 else 0.0,
                "passed": totals["passed"],
                "failed": totals["failed"],
                "total": total,
                "weight": self.category_weights.get(cat_name, 0),
            }
        
        return breakdown
