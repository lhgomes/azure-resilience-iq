"""
Patch resilience outputs to reflect multi-zone backend pools without rerunning collector.

This script:
1. Loads resources.json for a subscription
2. Updates VMSS Flex recommendation results for VMs in multi-zone backend pools
3. Re-runs zonal analysis (local only) and refreshes ZoneRecommendation checks

It does NOT call Azure Resource Graph or the collector.
"""


import argparse
from pathlib import Path
from typing import Dict, Any, List, Set

from app.logger import setup_logging, get_logger
from app.storage._json_repo import read_json, write_json
from app.resilience.run import analyze_and_save_zonal_resilience
from app.collector.arg import populate_backend_pool_ids

LOGGER = get_logger(__name__)

VMSS_RECOMMENDATION_ID = "5f2613df-629f-4b07-9425-2a47ea0dfad3"


def _collect_backend_pool_zones(resources: List[Dict[str, Any]]) -> Dict[str, Set[str]]:
    """Build a map of backend pool ID -> set of zones from VMs in that pool."""
    backend_pool_zones: Dict[str, Set[str]] = {}
    for resource in resources:
        rtype = resource.get("type", "").lower()
        if "microsoft.compute/virtualmachines" not in rtype or "/extensions" in rtype:
            continue

        backend_pool_ids = resource.get("backend_pool_ids", [])
        vm_zones = resource.get("zones", [])
        if not isinstance(backend_pool_ids, list) or not backend_pool_ids:
            continue

        zones = [str(z) for z in vm_zones] if isinstance(vm_zones, list) else []
        for pool_id in backend_pool_ids:
            if not isinstance(pool_id, str):
                continue
            backend_pool_zones.setdefault(pool_id, set()).update(zones)

    return backend_pool_zones


def _collect_vm_multizone_pool_map(
    resources: List[Dict[str, Any]],
    backend_pool_zones: Dict[str, Set[str]],
) -> Dict[str, bool]:
    """Map VM ID -> True if VM is in any backend pool spanning 2+ zones."""
    vm_multizone_pool: Dict[str, bool] = {}
    for resource in resources:
        rtype = resource.get("type", "").lower()
        if "microsoft.compute/virtualmachines" not in rtype or "/extensions" in rtype:
            continue

        vm_id = resource.get("id")
        if not vm_id:
            continue

        backend_pool_ids = resource.get("backend_pool_ids", [])
        is_multizone = False
        if isinstance(backend_pool_ids, list):
            for pool_id in backend_pool_ids:
                if not isinstance(pool_id, str):
                    continue
                zones = backend_pool_zones.get(pool_id, set())
                if len(zones) >= 2:
                    is_multizone = True
                    break
        vm_multizone_pool[vm_id.lower()] = is_multizone

    return vm_multizone_pool


def _update_vmss_recommendations(
    evaluations: Dict[str, Any],
    vm_multizone_pool: Dict[str, bool],
) -> int:
    """Set VMSS recommendation to pass for VMs in multi-zone backend pools."""
    updated = 0
    for resource_id, evaluation in evaluations.items():
        checks = evaluation.get("checks") or evaluation.get("findings") or []
        if not checks:
            continue

        if not vm_multizone_pool.get(resource_id.lower()):
            continue

        for check in checks:
            if check.get("recommendation_id") != VMSS_RECOMMENDATION_ID:
                continue
            if check.get("status") == "pass":
                continue
            check["status"] = "pass"
            # Keep validation_source as-is (typically "APRL")
            # BackendPoolZone analysis is an internal implementation detail
            updated += 1

    return updated


def _refresh_zone_recommendations(
    evaluations: Dict[str, Any],
    zone_recommendations_by_resource: Dict[str, List[Dict[str, Any]]],
) -> None:
    """Replace existing ZoneRecommendation checks with updated ones."""
    for resource_id, evaluation in evaluations.items():
        checks = evaluation.get("checks") or evaluation.get("findings") or []
        if not checks:
            continue

        filtered = []
        for check in checks:
            sources = check.get("validation_source")
            is_zone_rec = False
            if isinstance(sources, list) and "ZoneRecommendation" in sources:
                is_zone_rec = True
            elif sources == "ZoneRecommendation":
                is_zone_rec = True
            if not is_zone_rec:
                filtered.append(check)

        updated = zone_recommendations_by_resource.get(resource_id, [])
        evaluation["checks"] = filtered + updated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Patch resilience outputs for multi-zone backend pools without rerunning collector."
    )
    parser.add_argument("--subscription-id", required=True, help="Subscription ID")
    parser.add_argument(
        "--data-dir",
        default=str(Path(__file__).resolve().parents[2] / "data"),
        help="Base data directory (default: backend/data)",
    )
    parser.add_argument("--log-level", default="INFO", help="Log level")
    args = parser.parse_args()

    setup_logging(args.log_level)

    subscription_id = args.subscription_id
    data_root = Path(args.data_dir)
    data_dir = data_root / subscription_id
    resources_path = data_dir / "resources.json"
    evals_path = data_dir / "resilience_evaluations.json"

    resources_payload = read_json(resources_path, default={})
    resources = resources_payload.get("resources", [])
    if not resources:
        raise SystemExit(f"No resources found at {resources_path}")

    # Ensure backend_pool_ids are populated in-memory for LB/AppGW correlation
    populate_backend_pool_ids(resources)

    evals_payload = read_json(evals_path, default={})
    evaluations = evals_payload.get("evaluations")
    if evaluations is None:
        raise SystemExit(f"No evaluations found at {evals_path}")

    backend_pool_zones = _collect_backend_pool_zones(resources)
    vm_multizone_pool = _collect_vm_multizone_pool_map(resources, backend_pool_zones)

    updated = _update_vmss_recommendations(evaluations, vm_multizone_pool)
    LOGGER.info("Updated %s VMSS Flex checks based on multi-zone backend pools", updated)

    zone_recommendations_by_resource = analyze_and_save_zonal_resilience(
        subscription_id=subscription_id,
        resources=resources,
        data_dir=data_dir,
        resilience_evaluations=evals_payload,
    )
    _refresh_zone_recommendations(evaluations, zone_recommendations_by_resource)

    evals_payload["evaluations"] = evaluations
    write_json(evals_path, evals_payload)
    LOGGER.info("Patched resilience_evaluations.json for subscription %s", subscription_id)


if __name__ == "__main__":
    main()
