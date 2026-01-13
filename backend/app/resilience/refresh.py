"""
Refresh module for re-annotation and re-scoring after user changes.

Handles the complete refresh pipeline:
1. Load current graph snapshot with user overrides
2. Re-run LLM annotation with user context
3. Recalculate criticality weights based on new scores
4. Save updated annotations
5. Re-run scoring with new weights
6. Save updated evaluation results
"""

import json
import logging
from typing import Dict, Any

from app.config import get_subscription_dir, get_resources_path
from app.graph.from_azure import build_graph_from_resources
from app.llm.annotator import annotate_graph
from app.settings import get_settings, load_settings
from app.storage.llm_annotations_store import (
    save_llm_annotations,
    load_llm_annotations,
)
from app.storage.resilience_evaluations_store import (
    load_resilience_evaluations,
    save_resilience_evaluations,
)
from app.storage.node_overrides_store import load_node_overrides
from app.storage.edge_overrides_store import load_overrides
from app.storage.manual_edges_store import load_manual_edges
from app.resilience.aprl_integration import load_aprl_catalog, APRLEvaluator
from app.resilience.scorer import ResilienceScorer

LOGGER = logging.getLogger(__name__)


def refresh_annotations_and_scores(subscription_id: str) -> Dict[str, Any]:
    """
    Complete refresh pipeline: re-annotate with user context + re-score.

    Args:
        subscription_id: Target subscription

    Returns:
        Dict with status, counts, and any errors

    Raises:
        FileNotFoundError: If required data files don't exist
        Exception: If LLM or scoring fails
    """
    try:
        # Load settings
        load_settings()
        settings = get_settings()

        LOGGER.info("Starting annotation + scoring refresh for %s", subscription_id)

        # ============================================================
        # STEP 1: Load current graph with user context
        # ============================================================
        LOGGER.debug("Loading graph with user overrides and manual edges...")

        resources_path = get_resources_path(subscription_id)
        if not resources_path.exists():
            raise FileNotFoundError(
                f"Resources not found at {resources_path}. "
                f"Run collector first."
            )

        resources_data = json.loads(resources_path.read_text())
        if isinstance(resources_data, dict) and "resources" in resources_data:
            resources = resources_data["resources"]
        else:
            resources = resources_data

        # Build graph from resources
        graph = build_graph_from_resources(resources, subscription_id)

        # Load user overrides
        node_overrides = load_node_overrides(subscription_id)
        edge_overrides = load_overrides(subscription_id)
        manual_edges = load_manual_edges(subscription_id)

        # Merge user context into graph
        graph = _merge_user_context(graph, node_overrides, edge_overrides, manual_edges)

        LOGGER.info(
            "Graph loaded: %d nodes, %d edges (after user merges)",
            len(graph["nodes"]),
            len(graph["edges"]),
        )

        # ============================================================
        # STEP 2: Re-run LLM annotator with user context
        # ============================================================
        LOGGER.info("Re-running LLM annotator with user context...")

        annotations = annotate_graph(graph)
        node_count = len(annotations.nodes)
        edge_count = len(annotations.edges)

        if not annotations.nodes:
            LOGGER.warning("Annotator returned no node annotations")

        LOGGER.info(
            "✓ Re-annotation complete: %d nodes, %d edge suggestions",
            node_count,
            edge_count,
        )

        # ============================================================
        # STEP 3: Save updated annotations
        # ============================================================
        LOGGER.debug("Saving updated annotations...")
        save_llm_annotations(subscription_id, annotations)
        LOGGER.info("✓ Annotations saved")

        # ============================================================
        # STEP 4: Re-run resilience scoring
        # ============================================================
        LOGGER.info("Re-running resilience scoring...")

        # Load current evaluations
        evaluations = load_resilience_evaluations(subscription_id)
        if not evaluations:
            LOGGER.warning("No existing evaluations found; skipping re-scoring")
            return {
                "status": "partial",
                "message": "Re-annotation complete; no evaluations to re-score",
                "nodes_annotated": node_count,
                "edges_suggested": edge_count,
            }

        # Get category weights from settings
        category_weights = settings.get_category_weights()

        # Get updated component weights from new annotations
        component_weights = {}
        if hasattr(annotations, "nodes"):
            for node in annotations.nodes:
                if node.annotations.criticality_weight:
                    component_weights[node.node_id] = node.annotations.criticality_weight

        # Initialize scorer with new hierarchical model
        # Note: component_weights are now per-resource in data format
        scorer = ResilienceScorer(category_weights=category_weights)

        # Re-calculate scores for all evaluations
        updated_evaluations = _recalculate_scores(
            evaluations, scorer, component_weights
        )

        # Save updated evaluations
        save_resilience_evaluations(subscription_id, updated_evaluations)
        LOGGER.info("✓ Resilience scores updated and saved")

        # ============================================================
        # STEP 5: Return summary
        # ============================================================
        return {
            "status": "success",
            "message": "Annotations and scores refreshed successfully",
            "nodes_annotated": node_count,
            "edges_suggested": edge_count,
            "evaluations_updated": len(updated_evaluations.get("evaluations", {})),
        }

    except Exception as e:
        LOGGER.exception("Refresh failed: %s", str(e))
        raise


def _merge_user_context(
    graph: Dict[str, Any],
    node_overrides: Dict[str, Any],
    edge_overrides: Dict[str, Any],
    manual_edges: list,
) -> Dict[str, Any]:
    """
    Merge user overrides and manual edges into graph snapshot for LLM.

    Args:
        graph: Base graph from resources
        node_overrides: User node changes
        edge_overrides: User edge changes
        manual_edges: User-created edges

    Returns:
        Enhanced graph with user context
    """
    # Add user override metadata to nodes
    for node in graph.get("nodes", []):
        node_id = node.get("id")
        if node_id in node_overrides:
            override = node_overrides[node_id]
            # Store override as metadata for LLM context
            if "metadata" not in node:
                node["metadata"] = {}
            node["metadata"]["user_override"] = override

            # If user set criticality_score, include it
            if "criticality_score" in override:
                node["metadata"]["criticality_override"] = override["criticality_score"]

    # Add user-created edges to graph
    existing_edge_ids = {e.get("id") for e in graph.get("edges", [])}
    for manual_edge in manual_edges:
        # Create edge in graph if not already present
        edge_id = f"{manual_edge['source']}-{manual_edge['target']}"
        if edge_id not in existing_edge_ids:
            graph["edges"].append(
                {
                    "id": edge_id,
                    "source": manual_edge["source"],
                    "target": manual_edge["target"],
                    "relationship": manual_edge.get("relationship", "depends on"),
                    "confidence": 1.0,
                    "origin": "manual",
                    "source_kind": "manual",
                    "status": "accepted",
                }
            )
            existing_edge_ids.add(edge_id)

    return graph


def _recalculate_scores(
    evaluations: Dict[str, Any],
    scorer: ResilienceScorer,
    component_weights: Dict[str, float],
) -> Dict[str, Any]:
    """
    Recalculate resilience scores with updated component weights.

    Args:
        evaluations: Existing evaluation results from resilience_evaluations.json
        scorer: ResilienceScorer instance with updated component weights
        component_weights: Updated component weights from LLM

    Returns:
        Evaluations with updated scores
    """
    eval_list = evaluations.get("evaluations", {})

    # Convert evaluations to new scorer format with impact field
    # Format: {"resources": [...]} with full checks including impact
    resources = []
    
    for resource_id, evaluation in eval_list.items():
        # Build checks list with impact field
        checks = []
        for check in evaluation.get("checks", []):
            impact = check.get("impact", "Medium")  # Extract or default
            checks.append({
                "recommendation_id": check.get("recommendation_id", ""),
                "description": check.get("description", ""),
                "category": check.get("category", "Unknown"),
                "impact": impact,
                "status": check.get("status", "unknown")
            })
        
        resources.append({
            "id": resource_id,
            "name": evaluation.get("resource_name", ""),
            "type": evaluation.get("resource_type", "unknown"),
            "component_weight": evaluation.get("component_weight", 1.0),
            "checks": checks
        })

    # Re-calculate scores with new hierarchical interface
    scorer_input = {"resources": resources}
    scoring_results = scorer.score_workload(scorer_input)

    # Update workload-level scores with new field names
    evaluations["workload_score"] = scoring_results.get("workload_score")
    evaluations["category_breakdown"] = scoring_results.get("category_breakdown")
    evaluations["scoring_timestamp"] = scoring_results.get("scoring_timestamp")

    # Update each resource evaluation with new structure
    resources_map = {
        res["id"]: res for res in scoring_results.get("resources", [])
    }

    for resource_id, evaluation in eval_list.items():
        if resource_id in resources_map:
            resource_result = resources_map[resource_id]
            eval_list[resource_id]["component_score"] = resource_result.get("component_score")
            eval_list[resource_id]["component_weight"] = resource_result.get("component_weight")
            eval_list[resource_id]["categories"] = resource_result.get("categories")
            eval_list[resource_id]["checks_detail"] = resource_result.get("checks_detail")
            eval_list[resource_id]["scoring_timestamp"] = (
                scoring_results.get("scoring_timestamp")
            )

    return evaluations
