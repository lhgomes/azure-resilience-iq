"""
LLM Annotator CLI runner.

Usage:
  python -m app.llm.run --subscription-id ebb79bc0-aa86-44a7-8111-cabbe0c43993

This script:
1. Loads the graph from the collector output (data/{subscription_id}/resources/{subscription_id}.json)
2. Runs the LLM annotator to generate architecture suggestions
3. Saves annotations to data/{subscription_id}/llm_annotations/{subscription_id}.json
4. Reports success/failures

Requires:
- collector output file (from app.collector.run)

- Azure OpenAI and LLM settings configured in config/app_config.yaml
- az login (for DefaultAzureCredential) if api_key not provided

Paths:
- Base data directory configured in config/app_config.yaml (data.dir)
"""

import json
import argparse

from app.graph.from_azure import build_graph_from_resources
from app.config import get_resources_path
from app.llm.annotator import annotate_graph
from app.storage.llm_annotations_store import save_llm_annotations
from app.settings import load_settings, get_settings
from app.logger import setup_logging, get_logger

LOGGER = get_logger(__name__)

def main():
    parser = argparse.ArgumentParser(
        description="Run LLM annotator on collected resources"
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
    
    # Load settings and configure logging with optional CLI override
    load_settings()
    settings = get_settings()
    setup_logging(args.log_level)

    LOGGER.info("Starting LLM annotator for subscription: %s", args.subscription_id)

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
        # Load resources and build graph
        LOGGER.info("Loading resources from %s", resources_path)
        resources_data = json.loads(resources_path.read_text())
        
        # Handle new format with subscription metadata
        if isinstance(resources_data, dict) and "resources" in resources_data:
            all_resources = resources_data["resources"]
        else:
            # Fallback for direct list format
            all_resources = resources_data
        
        # Filter to only monitored resources for LLM evaluation
        # Non-monitored resources are kept in the graph for topology/relationship mapping
        resources = [r for r in all_resources if r.get("monitored", True)]
        non_monitored_count = len(all_resources) - len(resources)
        if non_monitored_count > 0:
            LOGGER.info(f"Filtering out {non_monitored_count} non-monitored resources for LLM evaluation")
        
        # Keep all resources for graph building (to preserve relationships), 
        # but only annotate monitored ones
        graph = build_graph_from_resources(all_resources, args.subscription_id)
        LOGGER.info("Loaded graph with %d total nodes and %d edges", 
                    len(graph["nodes"]), len(graph["edges"]))
        LOGGER.info("Annotating %d monitored resources", len(resources))

        # Run annotator
        LOGGER.info("Running LLM annotator...")
        annotations = annotate_graph(graph)

        if not annotations.nodes and not annotations.edges:
            LOGGER.warning("Annotator returned no results; check configuration")
            return 1

        # Save annotations
        LOGGER.info("Saving annotations...")
        save_llm_annotations(args.subscription_id, annotations)

        LOGGER.info(
            "✓ Annotation complete: %d nodes, %d edge suggestions",
            len(annotations.nodes), len(annotations.edges)
        )
        return 0

    except Exception:
        LOGGER.exception("Annotator failed")
        return 1


if __name__ == "__main__":
    exit(main())
