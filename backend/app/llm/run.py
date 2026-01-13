"""
LLM Annotator CLI runner.

Usage:
  python -m app.llm.run --subscription-id 00000000-0000-0000-0000-000000000000

This script:
1. Loads the graph from the collector output (data/{subscription_id}/resources/{subscription_id}.json)
2. Runs the LLM annotator to generate architecture suggestions
3. Saves annotations to data/{subscription_id}/llm_annotations/{subscription_id}.json
4. Reports success/failures

Requires:
- collector output file (from app.collector.run)
- AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT (in .env or environment)
- USE_REAL_LLM=true (in .env or environment)
- az login (for DefaultAzureCredential)

Paths:
- Override the base data directory with AZURE_WORKLOAD_GRAPH_DATA_DIR (default: data)
"""

import json
import argparse

from dotenv import load_dotenv

from app.graph.from_azure import build_graph_from_resources
from app.config import get_resources_path
from app.llm.annotator import annotate_graph
from app.storage.llm_annotations_store import save_llm_annotations
from app.settings import load_settings, get_settings
from app.logger import setup_logging, get_logger

LOGGER = get_logger(__name__)

# Load environment variables from .env file
load_dotenv()


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
            resources = resources_data["resources"]
        else:
            # Fallback for direct list format
            resources = resources_data
        
        graph = build_graph_from_resources(resources, args.subscription_id)
        LOGGER.info("Loaded graph with %d nodes and %d edges", 
                    len(graph["nodes"]), len(graph["edges"]))

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
