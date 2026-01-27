"""
Integration module for resilience evaluation pipeline.

This module orchestrates the flow:
1. Collect (existing)
2. LLM annotations (existing)
3. Evaluate (using APRL)
4. Frontend calculates scores from check data
"""

from typing import Dict, Any, List, Optional
from app.resilience.evaluator import ResiliencyEvaluator
from app.models import WorkloadComponent
from app.settings import get_settings
import yaml
import os
import logging

LOGGER = logging.getLogger(__name__)


class ResiliencyPipeline:
    """
    Orchestrates the resilience analysis pipeline.
    
    Flow:
    1. Takes components from discovery/LLM
    2. Evaluates against APRL recommendations
    3. Returns results for frontend (frontend calculates scores)
    """
    
    def __init__(
        self,
        aprl_root: Optional[str] = None,
        rules_dir: Optional[str] = None,
        subscription_filter: Optional[str] = None,
        resource_group_filter: Optional[List[str]] = None,
    ):
        """
        Initialize the resilience pipeline.
        
        Args:
            aprl_root: Path to APRL v2 repository. If not provided, loads from config.
            rules_dir: Path to generated resiliency rules. If not provided, loads from config.
            subscription_filter: Optional subscription ID to filter
            resource_group_filter: Optional list of RG names to filter
        """
        # Load from config if not provided
        settings = get_settings()
        aprl_root = aprl_root or settings.get_aprl_root()
        rules_dir = rules_dir or settings.get_rules_dir()
        
        self.evaluator = ResiliencyEvaluator(
            aprl_root=aprl_root,
            rules_dir=rules_dir,
            subscription_filter=subscription_filter,
            resource_group_filter=resource_group_filter,
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
            Final results containing evaluation
        """
        # Step 1: Evaluate components against APRL
        print(f"Starting resilience evaluation for workload: {workload_name}")
        evaluation_results = self.evaluator.evaluate_workload(
            workload_name=workload_name,
            components=components,
        )
        
        # Step 2: Return evaluation results (frontend handles all scoring)
        print("Resiliency evaluation completed - scoring done client-side")
        
        final_output = {
            "workload_name": workload_name,
            "evaluation": evaluation_results,
        }
        
        print("Resiliency analysis completed successfully")
        return final_output
