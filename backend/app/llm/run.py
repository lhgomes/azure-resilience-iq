"""
LLM Annotator CLI runner.

Usage:
  python -m app.llm.run --workload-id demo

This script:
1. Loads the graph from data/collector/resources.json
2. Runs the LLM annotator to generate architecture suggestions
3. Saves annotations to data/llm_annotations/
4. Reports success/failures

Requires:
- data/collector/resources.json (from app.collector.run)
- AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT (in .env or environment)
- USE_REAL_LLM=true (in .env or environment)
- az login (for DefaultAzureCredential)
"""

import json
import logging
import argparse
from pathlib import Path

from dotenv import load_dotenv

from app.graph.from_azure import build_graph_from_resources
from app.llm.annotator import annotate_graph
from app.storage.llm_annotations_store import save_llm_annotations

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
LOGGER = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

COLLECTOR_RESOURCES = Path("data/collector/resources.json")


def main():
    parser = argparse.ArgumentParser(
        description="Run LLM annotator on collected resources"
    )
    parser.add_argument(
        "--workload-id",
        default="demo",
        help="Workload ID (default: demo)"
    )
    args = parser.parse_args()

    LOGGER.info("Starting LLM annotator for workload: %s", args.workload_id)

    # Check if resources exist
    if not COLLECTOR_RESOURCES.exists():
        LOGGER.error(
            "Collector resources not found at %s. "
            "Run 'python -m app.collector.run --subscription-id <id>' first.",
            COLLECTOR_RESOURCES
        )
        return 1

    try:
        # Load resources and build graph
        LOGGER.info("Loading resources from %s", COLLECTOR_RESOURCES)
        resources = json.loads(COLLECTOR_RESOURCES.read_text())
        graph = build_graph_from_resources(resources, args.workload_id)
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
        save_llm_annotations(args.workload_id, annotations)

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
