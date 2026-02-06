#!/usr/bin/env python3
"""
Test script to demonstrate zonal resilience analysis.

This script loads resources from a subscription and runs the zonal
analysis to generate the zonal_resilience.json file.
"""

import sys
import json
from pathlib import Path

# Add backend to path
backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))

from app.resilience.zonal_analyzer import ZonalAnalyzer, ZonalResiliencySummary
from datetime import datetime, timezone


def test_zonal_analysis(subscription_id: str):
    """Test zonal analysis with real subscription data."""
    
    print(f"Testing zonal analysis for subscription: {subscription_id}")
    print("=" * 70)
    
    # Load resources
    resources_file = backend_dir / "data" / subscription_id / "resources.json"
    if not resources_file.exists():
        print(f"ERROR: Resources file not found: {resources_file}")
        return 1
    
    with open(resources_file, 'r') as f:
        resources_data = json.load(f)
    
    # Handle new format with subscription metadata
    if isinstance(resources_data, dict) and "resources" in resources_data:
        resources = resources_data["resources"]
    else:
        resources = resources_data
    
    print(f"Loaded {len(resources)} resources\n")
    
    # Analyze each resource
    zonal_data_list = []
    
    for resource in resources:
        try:
            zonal_data = ZonalAnalyzer.extract_zonal_data(resource)
            zonal_data_list.append({
                "resource_id": resource.get("id"),
                "resource_name": resource.get("name"),
                "resource_type": resource.get("type"),
                "location": resource.get("location"),
                "zonal_data": {
                    "zones_used": zonal_data.zones_used,
                    "is_zone_redundant": zonal_data.is_zone_redundant,
                    "zone_count": zonal_data.zone_count,
                    "meets_3az_requirement": zonal_data.meets_3az_requirement,
                    "deployment_pattern": zonal_data.deployment_pattern.value,
                    "recommendation": zonal_data.recommendation,
                }
            })
        except Exception as e:
            print(f"Warning: Failed to analyze {resource.get('id')}: {e}")
    
    # Calculate summary (pass resources for region analysis)
    all_zonal = [ZonalAnalyzer.extract_zonal_data(r) for r in resources]
    summary = ZonalResiliencySummary(all_zonal, resources)
    
    # Print summary
    print("ZONAL RESILIENCE SUMMARY")
    print("-" * 70)
    print(f"Total resources:           {summary.total_resources}")
    print(f"Zone-redundant resources:  {summary.zone_redundant_count}")
    print(f"Multi-zone resources:      {summary.multi_zone_count}")
    print(f"Single-zone resources:     {summary.single_zone_count}")
    print(f"Not applicable (N/A):      {summary.not_applicable_count}")
    print(f"Unknown zone resources:    {summary.unknown_count}")
    print(f"3-AZ compliant resources:  {summary.compliant_3az_count}")
    print(f"Overall 3-AZ compliant:    {summary.is_3az_compliant}")
    print(f"Zonal resilience score:    {summary.zonal_resilience_score:.1%}")
    total_applicable = summary.total_resources - summary.not_applicable_count
    print(f"Compliance percentage:     {(summary.compliant_3az_count/total_applicable*100) if total_applicable > 0 else 0:.1f}%")
    
    # Print regional analysis if available
    if summary.regions_analyzed:
        print()
        print("REGIONAL ANALYSIS")
        print("-" * 70)
        print(f"Total regions:             {len(summary.regions_analyzed)}")
        print(f"Zone-enabled regions:      {len(summary.zone_enabled_regions)}")
        print(f"Non-zone regions:          {len(summary.non_zone_regions)}")
        print(f"Resources in zone regions: {summary.resources_in_zone_regions}")
        print(f"Resources in non-zone:     {summary.resources_in_non_zone_regions}")
        if summary.zone_enabled_regions:
            print(f"\nZone-enabled: {', '.join(summary.zone_enabled_regions)}")
        if summary.non_zone_regions:
            print(f"Non-zone:     {', '.join(summary.non_zone_regions)}")
    print()
    
    # Show some example resources
    print("EXAMPLE RESOURCE ANALYSIS")
    print("-" * 70)
    for i, item in enumerate(zonal_data_list[:5]):  # Show first 5
        print(f"\n{i+1}. {item['resource_name']}")
        print(f"   Type: {item['resource_type']}")
        zd = item['zonal_data']
        print(f"   Pattern: {zd['deployment_pattern']}")
        if zd['zones_used']:
            print(f"   Zones: {', '.join(zd['zones_used'])}")
        print(f"   3-AZ Compliant: {'✓' if zd['meets_3az_requirement'] else '✗'}")
        print(f"   {zd['recommendation']}")
    
    # Save results
    results = {
        "subscription_id": subscription_id,
        "analysis_timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": summary.to_dict(),
        "resources": zonal_data_list,
    }
    
    output_file = backend_dir / "data" / subscription_id / "zonal_resilience.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\n{'=' * 70}")
    print(f"✓ Analysis complete! Results saved to:")
    print(f"  {output_file}")
    print(f"\nTest the API endpoint:")
    print(f"  curl http://localhost:8000/api/subscriptions/{subscription_id}/zonal-resilience")
    
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sub_id = sys.argv[1]
    else:
        # Default to the subscription that has data
        sub_id = "bab86631-7bdc-42ec-8760-30baaf61fad1"
    
    sys.exit(test_zonal_analysis(sub_id))
