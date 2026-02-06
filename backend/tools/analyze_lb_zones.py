#!/usr/bin/env python3
"""
Load Balancer and Application Gateway zone configuration analysis.

Analyzes existing resources.json to classify LB/APGW zone resilience based on:
1. SKU and zones property
2. Frontend IP configuration
3. Public IP zone matching

Updates zonal_resilience.json with computed zonal_data for LB/APGW resources.

Usage:
    python backend/tools/analyze_lb_zones.py --subscription-id <subscription-id>
"""

import json
import sys
from pathlib import Path

# Add backend to path
backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))

from app.collector.load_balancer_analyzer import LoadBalancerAnalyzer, LBZoneAnalysis
from app.logger import setup_logging, get_logger

LOGGER = get_logger(__name__)


def _build_zonal_data(analysis: LBZoneAnalysis) -> dict:
    zones_used = analysis.direct_zones or []
    if isinstance(zones_used, list):
        zones_used = [str(z) for z in zones_used]
    else:
        zones_used = []

    if analysis.classification == 'zone_redundant':
        return {
            'zones_used': [],
            'is_zone_redundant': True,
            'zone_count': 0,
            'meets_3az_requirement': True,
            'deployment_pattern': 'zone_redundant'
        }

    if analysis.classification == 'zonal':
        zone_count = len(zones_used)
        if zone_count >= 2:
            deployment_pattern = 'multi_zone'
        elif zone_count == 1:
            deployment_pattern = 'single_zone'
        else:
            deployment_pattern = 'unknown'

        return {
            'zones_used': zones_used,
            'is_zone_redundant': False,
            'zone_count': zone_count,
            'meets_3az_requirement': zone_count >= 3,
            'deployment_pattern': deployment_pattern
        }

    if analysis.classification == 'not_zone_resilient':
        return {
            'zones_used': [],
            'is_zone_redundant': False,
            'zone_count': 1,
            'meets_3az_requirement': False,
            'deployment_pattern': 'single_zone'
        }

    return {
        'zones_used': [],
        'is_zone_redundant': False,
        'zone_count': 0,
        'meets_3az_requirement': False,
        'deployment_pattern': 'unknown'
    }


def analyze_lb_zones_for_subscription(subscription_id: str) -> bool:
    """
    Analyze LB and APGW zones in existing resources.json.
    
    Updates zonal_resilience.json zonal_data for each LB/APGW resource.
    Does NOT modify resources.json or any 'zones' fields.
    """
    
    # Load resources
    resources_file = backend_dir / "data" / subscription_id / "resources.json"
    if not resources_file.exists():
        LOGGER.error(f"Resources file not found: {resources_file}")
        return False
    
    LOGGER.info(f"Loading resources from {resources_file}")
    with open(resources_file, 'r') as f:
        data = json.load(f)
    
    resources = data.get('resources', [])
    LOGGER.info(f"Loaded {len(resources)} resources")

    zonal_file = backend_dir / "data" / subscription_id / "zonal_resilience.json"
    if not zonal_file.exists():
        LOGGER.error(f"Zonal resilience file not found: {zonal_file}")
        return False

    LOGGER.info(f"Loading zonal resilience data from {zonal_file}")
    with open(zonal_file, 'r') as f:
        zonal_data = json.load(f)

    zonal_resources = zonal_data.get('resources', [])
    zonal_resource_map = {
        str(item.get('resource_id', '')).lower(): item
        for item in zonal_resources
        if item.get('resource_id')
    }
    
    # Build resource lookup map
    resources_map = {r.get('id', ''): r for r in resources if r.get('id')}
    
    # Find LBs and APGWs
    load_balancers = [
        r for r in resources
        if 'microsoft.network/loadbalancers' in r.get('type', '').lower()
    ]
    app_gateways = [
        r for r in resources
        if 'microsoft.network/applicationgateways' in r.get('type', '').lower()
    ]
    
    LOGGER.info(f"Found {len(load_balancers)} Load Balancers")
    LOGGER.info(f"Found {len(app_gateways)} Application Gateways")
    
    # Analyze each LB
    lb_count = 0
    for lb in load_balancers:
        try:
            analysis = LoadBalancerAnalyzer.analyze_load_balancer(lb, resources_map)

            zonal_entry = zonal_resource_map.get(str(analysis.resource_id).lower())
            if not zonal_entry:
                LOGGER.warning(f"  ✗ Missing zonal entry for {analysis.resource_name}")
                continue

            zonal_entry['zonal_data'] = _build_zonal_data(analysis)
            
            LOGGER.info(f"  ✓ {analysis.resource_name}: {analysis.classification}")
            if analysis.recommendation:
                LOGGER.info(f"      → {analysis.recommendation}")
            
            lb_count += 1
        except Exception as e:
            LOGGER.warning(f"  ✗ Failed to analyze {lb.get('name', 'unknown')}: {e}")
    
    # Analyze each APGW
    apgw_count = 0
    for apgw in app_gateways:
        try:
            analysis = LoadBalancerAnalyzer.analyze_application_gateway(apgw, resources_map)

            zonal_entry = zonal_resource_map.get(str(analysis.resource_id).lower())
            if not zonal_entry:
                LOGGER.warning(f"  ✗ Missing zonal entry for {analysis.resource_name}")
                continue

            zonal_entry['zonal_data'] = _build_zonal_data(analysis)
            
            LOGGER.info(f"  ✓ {analysis.resource_name}: {analysis.classification}")
            if analysis.recommendation:
                LOGGER.info(f"      → {analysis.recommendation}")
            
            apgw_count += 1
        except Exception as e:
            LOGGER.warning(f"  ✗ Failed to analyze {apgw.get('name', 'unknown')}: {e}")
    
    # Save updated zonal resilience data
    LOGGER.info(f"💾 Saving updated zonal data to {zonal_file}")
    zonal_data['resources'] = zonal_resources
    with open(zonal_file, 'w') as f:
        json.dump(zonal_data, f, indent=2)
    
    LOGGER.info(f"✅ Complete!")
    LOGGER.info(f"   Analyzed {lb_count} Load Balancers")
    LOGGER.info(f"   Analyzed {apgw_count} Application Gateways")
    LOGGER.info(f"   Updated zonal_data in zonal_resilience.json")
    
    return True


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Analyze Load Balancer and Application Gateway zone configuration"
    )
    parser.add_argument("--subscription-id", required=True, help="Subscription ID")
    parser.add_argument("--log-level", default="INFO", help="Log level")
    
    args = parser.parse_args()
    
    setup_logging(args.log_level)
    
    success = analyze_lb_zones_for_subscription(args.subscription_id)
    sys.exit(0 if success else 1)
