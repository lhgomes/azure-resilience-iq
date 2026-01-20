#!/usr/bin/env python
"""
Regenerate the monitored (HA/DR) resource-type allowlist from APRL recommendations.

Outputs:
- backend/config/monitored_resource_types.yaml (consumed by collector & Terraform)
- ai-context/ha-dr-recommendation-resource-types.txt (human-readable list)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

import yaml

CONTROLS = {"HighAvailability", "DisasterRecovery"}
CONFIG_FILENAME = "monitored_resource_types.yaml"
TXT_FILENAME = "ha-dr-recommendation-resource-types.txt"


def collect_resource_types(aprl_root: Path) -> List[str]:
    """Scan APRL recommendation files and return unique HA/DR resource types."""
    seen: Dict[str, str] = {}

    for rec_file in aprl_root.rglob("recommendations.yaml"):
        data = yaml.safe_load(rec_file.read_text(encoding="utf-8")) or []
        if not isinstance(data, list):
            continue

        for rec in data:
            if not isinstance(rec, dict):
                continue
            if rec.get("recommendationControl") not in CONTROLS:
                continue
            resource_type = rec.get("recommendationResourceType")
            if not resource_type:
                continue
            key = resource_type.lower()
            seen.setdefault(key, resource_type)

    # Sort by lowercase for stable output
    return sorted(seen.values(), key=lambda v: v.lower())


def write_outputs(repo_root: Path, resource_types: List[str]) -> None:
    backend_root = repo_root / "backend"

    config_path = backend_root / "config" / CONFIG_FILENAME
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_payload = {"monitored_resource_types": resource_types}
    config_path.write_text(yaml.safe_dump(config_payload, sort_keys=False), encoding="utf-8")

    txt_path = repo_root / "ai-context" / TXT_FILENAME
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text("\n".join(resource_types) + "\n", encoding="utf-8")

    print(f"✓ Wrote {len(resource_types)} resource types to {config_path}")
    print(f"✓ Wrote {len(resource_types)} resource types to {txt_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh HA/DR resource-type allowlist from APRL")
    parser.add_argument(
        "--aprl-root",
        type=Path,
        default=None,
        help="Path to APRL root (defaults to ../aprl relative to backend)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root (defaults to three levels up from this script)",
    )
    args = parser.parse_args()

    script_path = Path(__file__).resolve()
    default_repo_root = script_path.parents[3]
    repo_root = (args.repo_root or default_repo_root).resolve()
    backend_root = repo_root / "backend"
    aprl_root = (args.aprl_root or (backend_root / "aprl")).resolve()

    azure_resources_root = aprl_root / "azure-resources"
    if not azure_resources_root.exists():
        raise SystemExit(f"APRL azure-resources directory not found: {azure_resources_root}")

    resource_types = collect_resource_types(azure_resources_root)
    if not resource_types:
        raise SystemExit("No HA/DR resource types found; aborting to avoid empty allowlist")

    write_outputs(repo_root, resource_types)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
