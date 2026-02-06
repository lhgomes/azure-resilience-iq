#!/usr/bin/env python3
"""
Normalize resilience_evaluations.json validation_source to a string.

Converts array values to a single string using the same hierarchy as
cleanup_validation_source.py (LLM > Heuristic > APRL > ZoneRecommendation).
"""

import argparse
from pathlib import Path

from app.logger import setup_logging, get_logger
from app.resilience.cleanup_validation_source import cleanup_validation_sources

LOGGER = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize validation_source arrays to a single string"
    )
    parser.add_argument("--subscription-id", required=True, help="Subscription ID")
    parser.add_argument(
        "--data-dir",
        default=str(Path(__file__).resolve().parents[1] / "data"),
        help="Base data directory (default: backend/data)",
    )
    parser.add_argument("--log-level", default="INFO", help="Log level")
    args = parser.parse_args()

    setup_logging(args.log_level)

    data_dir = Path(args.data_dir) / args.subscription_id
    if not data_dir.exists():
        raise SystemExit(f"Data directory not found: {data_dir}")

    LOGGER.info("Normalizing validation_source values in %s", data_dir)
    cleanup_validation_sources(data_dir)


if __name__ == "__main__":
    main()
