"""
Cleanup script to convert validation_source arrays to single top-level validation strategy.

Validation hierarchy: LLM > Heuristic > APRL > ZoneRecommendation
- validation_source should be a string, not an array
- Use the highest-level validation that was actually executed
"""
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
LOGGER = logging.getLogger(__name__)


def cleanup_validation_sources(data_dir: Path) -> None:
    """
    Remove arrays from validation_source and use only top-level validation strategy.
    
    Hierarchy: LLM > Heuristic > APRL > ZoneRecommendation
    - If array contains "LLM", use "LLM"
    - Else if array contains "Heuristic", use "Heuristic"
    - Else if array contains "APRL", use "APRL"
    - Else if array contains "ZoneRecommendation", use "ZoneRecommendation"
    - Otherwise use the first item
    """
    eval_file = data_dir / "resilience_evaluations.json"
    
    if not eval_file.exists():
        LOGGER.error(f"File not found: {eval_file}")
        return
    
    with open(eval_file) as f:
        data = json.load(f)
    
    updated_count = 0
    for resource_id, evaluation in data.get("evaluations", {}).items():
        checks = evaluation.get("checks", [])
        for check in checks:
            sources = check.get("validation_source")
            if not sources:
                continue
            
            if isinstance(sources, list):
                # Determine top-level source from hierarchy
                if "LLM" in sources:
                    top_source = "LLM"
                elif "Heuristic" in sources:
                    top_source = "Heuristic"
                elif "APRL" in sources:
                    top_source = "APRL"
                elif "ZoneRecommendation" in sources:
                    top_source = "ZoneRecommendation"
                else:
                    # Fallback to first item
                    top_source = sources[0]
                
                check["validation_source"] = top_source
                updated_count += 1
                LOGGER.debug(f"Converted {sources} → {top_source} for {check.get('recommendation_id')} in {resource_id}")
    
    # Write back
    with open(eval_file, "w") as f:
        json.dump(data, f, indent=2)
    
    LOGGER.info(f"Cleaned up {updated_count} validation_source entries")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Clean up BackendPoolZone from validation_source")
    parser.add_argument("--subscription-id", required=True, help="Azure subscription ID")
    parser.add_argument("--data-dir", default="data", help="Data directory path")
    
    args = parser.parse_args()
    
    data_dir = Path(args.data_dir) / args.subscription_id
    if not data_dir.exists():
        LOGGER.error(f"Data directory not found: {data_dir}")
        exit(1)
    
    cleanup_validation_sources(data_dir)
    LOGGER.info(f"Cleanup complete for subscription {args.subscription_id}")
