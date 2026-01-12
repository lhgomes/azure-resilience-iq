"""
Integration module for resilience evaluation and scoring pipeline.

This module orchestrates the flow:
1. Collect (existing)
2. LLM annotations (existing)
3. Evaluate (using APRL)
4. Score (calculate resilience)
"""

from typing import Dict, Any, List, Optional
from app.resilience.evaluator import ResilienceEvaluator
from app.resilience.scorer import ResilienceScorer
from app.models import WorkloadComponent
from app.settings import get_settings
import yaml
import os
import logging

LOGGER = logging.getLogger(__name__)


class ResiliencePipeline:
    """
    Orchestrates the resilience analysis pipeline.
    
    Flow:
    1. Takes components from discovery/LLM
    2. Evaluates against APRL recommendations
    3. Calculates resilience scores
    4. Outputs results for frontend
    """
    
    def __init__(
        self,
        aprl_root: Optional[str] = None,
        rules_dir: Optional[str] = None,
        category_weights: Optional[Dict[str, float]] = None,
        subscription_filter: Optional[str] = None,
        resource_group_filter: Optional[List[str]] = None,
    ):
        """
        Initialize the resilience pipeline.
        
        Args:
            aprl_root: Path to APRL v2 repository. If not provided, loads from config.
            rules_dir: Path to generated resiliency rules. If not provided, loads from config.
            category_weights: Resilience category weights. If not provided, loads from config.
            subscription_filter: Optional subscription ID to filter
            resource_group_filter: Optional list of RG names to filter
        """
        # Load from config if not provided
        settings = get_settings()
        aprl_root = aprl_root or settings.get_aprl_root()
        rules_dir = rules_dir or settings.get_rules_dir()
        category_weights = category_weights or settings.get_category_weights()
        
        self.evaluator = ResilienceEvaluator(
            aprl_root=aprl_root,
            rules_dir=rules_dir,
            subscription_filter=subscription_filter,
            resource_group_filter=resource_group_filter,
        )
        
        # Use provided or configured category weights
        self.category_weights = category_weights
        
        self.scorer = ResilienceScorer(
            category_weights=self.category_weights,
        )
    
    def run(
        self,
        workload_name: str,
        components: List[WorkloadComponent],
    ) -> Dict[str, Any]:
        """
        Run the complete resilience analysis pipeline.
        
        Args:
            workload_name: Name of the workload
            components: Components from discovery/LLM step
            
        Returns:
            Final results containing evaluation and scores
        """
        # Step 1: Evaluate components against APRL
        print(f"Starting resilience evaluation for workload: {workload_name}")
        evaluation_results = self.evaluator.evaluate_workload(
            workload_name=workload_name,
            components=components,
        )
        
        # Step 2: Score the results
        print("Calculating resilience scores...")
        scoring_results = self.scorer.score_workload(evaluation_results)
        
        # Step 3: Combine results
        final_output = {
            "workload_name": workload_name,
            "evaluation": evaluation_results,
            "scoring": scoring_results,
            "recommendations": self._generate_recommendations(scoring_results),
        }
        
        print("Resilience analysis completed successfully")
        return final_output
    
    def _generate_recommendations(self, scoring_results: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Generate actionable recommendations based on scores.
        
        Args:
            scoring_results: Results from scoring
            
        Returns:
            List of recommendations sorted by impact
        """
        recommendations = []
        
        # Analyze category scores
        category_scores = scoring_results.get("category_scores", {})
        
        for category, details in category_scores.items():
            score = details.get("score", 0)
            failed = details.get("failed", 0)
            
            # Generate recommendation if score is below 0.8 (80%) and there are failures
            if score < 0.8 and failed > 0:
                recommendations.append({
                    "category": category,
                    "priority": self._calculate_priority(score, failed),
                    "message": f"Improve {category}: {failed} checks failing",
                    "score": score,
                    "failing_checks": failed,
                })
        
        # Sort by priority (highest first)
        recommendations.sort(
            key=lambda x: x.get("priority", 0),
            reverse=True
        )
        
        return recommendations
    
    def _calculate_priority(self, score: float, failures: int) -> float:
        """
        Calculate recommendation priority (0.0 to 1.0).
        
        Args:
            score: Current score (0.0 to 1.0)
            failures: Number of failing checks
            
        Returns:
            Priority score
        """
        # Priority is higher for lower scores and more failures
        return (1.0 - score) * (1.0 + failures / 10.0)
