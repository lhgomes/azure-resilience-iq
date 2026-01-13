"""
Example integration of resilience analysis into the workload graph pipeline.

This demonstrates how to integrate the evaluation and scoring steps
into your existing Collect -> LLM -> Evaluate flow.
"""

from typing import List, Dict, Any
from app.resilience.pipeline import ResiliencePipeline
from app.models import WorkloadComponent
from app.graph.model import WorkloadGraph


async def analyze_workload_resilience(
    workload_graph: WorkloadGraph,
    workload_name: str,
    aprl_root: str = "./backend/aprl",
    rules_dir: str = "./config/resiliency_rules",
) -> Dict[str, Any]:
    """
    Analyze workload resilience as part of the overall pipeline.
    
    Flow:
    1. Collect resources (done by graph builder)
    2. LLM annotations (done by annotator)
    3. Evaluate against APRL (this function)
    
    Args:
        workload_graph: The built workload graph with components
        workload_name: Name of the workload
        aprl_root: Path to APRL v2 repository
        rules_dir: Path to resiliency rules
        
    Returns:
        Dictionary with resilience analysis results
        
    Example:
        # After building graph and running LLM:
        resilience_results = await analyze_workload_resilience(
            workload_graph=graph,
            workload_name="production-api",
            aprl_root="./backend/aprl",
            rules_dir="./config/resiliency_rules",
        )
        
        # Access results:
        recommendations = resilience_results["recommendations"]
    """
    
    # Initialize the resilience pipeline (loads settings from app config)
    pipeline = ResiliencePipeline(
        aprl_root=aprl_root,
        rules_dir=rules_dir,
    )
    
    # Convert graph components to WorkloadComponent format
    components = _extract_components_from_graph(workload_graph)
    
    # Run the evaluation and scoring pipeline
    resilience_analysis = pipeline.run(
        workload_name=workload_name,
        components=components,
    )
    
    return resilience_analysis


def _extract_components_from_graph(
    workload_graph: WorkloadGraph,
) -> List[WorkloadComponent]:
    """
    Extract components from the workload graph.
    
    Converts graph nodes into WorkloadComponent objects for evaluation.
    
    Args:
        workload_graph: The NetworkX-based workload graph
        
    Returns:
        List of WorkloadComponent objects
    """
    components = []
    
    for node_id, node_data in workload_graph.nodes(data=True):
        comp = WorkloadComponent(
            id=node_id,
            name=node_data.get("name", node_id),
            resource_type=node_data.get("resource_type", "Unknown"),
            properties={
                "has_redundancy": node_data.get("has_redundancy", False),
                "has_backup": node_data.get("has_backup", False),
                "monitoring_enabled": node_data.get("monitoring_enabled", False),
                # Add any other relevant properties from the graph
            },
        )
        components.append(comp)
    
    return components


# ============================================================================
# Integration points for your existing code
# ============================================================================

async def main_pipeline_example():
    """
    Complete example showing how resilience analysis fits into your workflow.
    
    Your existing flow:
    1. Collect (via graph builder) - DONE
    2. LLM annotations (via annotator) - DONE
    
    New flow addition:
    3. Evaluate against APRL - NEW
    4. Return to frontend - NEW
    """
    
    from app.graph.builder import WorkloadGraphBuilder
    from app.llm.annotator import LLMAnnotator
    from app.services.subscriptions import SubscriptionService
    from app.services.workloads import WorkloadService
    
    # Step 1: Collect (existing code)
    # graph_builder = WorkloadGraphBuilder(...)
    # graph = await graph_builder.build(...)
    
    # Step 2: LLM annotations (existing code)
    # annotator = LLMAnnotator(...)
    # annotated_graph = await annotator.annotate(graph)
    
    # Step 3 & 4: NEW - Resilience evaluation and scoring
    # resilience_results = await analyze_workload_resilience(
    #     workload_graph=annotated_graph,
    #     workload_name="my-workload",
    # )
    
    # Step 5: Return to frontend
    # return {
    #     "graph": annotated_graph,
    #     "resilience": resilience_results,
    #     "recommendations": resilience_results["recommendations"],
    # }
    
    pass
