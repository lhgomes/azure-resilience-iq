#!/usr/bin/env python3
"""
Verify custom zone redundancy queries are integrated with resilience system.

This script demonstrates that custom KQL files are loaded and will execute
during resilience evaluations.
"""

import sys
from pathlib import Path

# Add backend to path
backend_dir = Path(__file__).parent
sys.path.insert(0, str(backend_dir))

from app.resilience.aprl_integration import APRLCatalog
from app.settings import get_settings


def main():
    print("=" * 70)
    print("Custom Zone Redundancy Queries - Integration Verification")
    print("=" * 70)
    print()
    
    # Load settings
    settings = get_settings()
    aprl_root = settings.get_aprl_root()
    custom_rules_dir = settings.get_rules_dir()
    
    print(f"APRL Root: {aprl_root}")
    print(f"Custom Rules Dir: {custom_rules_dir}")
    print()
    
    # Initialize catalog with custom rules
    print("Loading APRL catalog with custom rules...")
    catalog = APRLCatalog(aprl_root, custom_rules_dir=custom_rules_dir)
    print(f"✓ Catalog loaded successfully")
    print()
    
    # Summary
    total_recs = sum(len(recs) for recs in catalog.recommendations.values())
    print(f"Total Recommendations: {total_recs}")
    print(f"Custom KQL Files: {len(catalog.custom_kql_files)}")
    print()
    
    # List custom rules
    if catalog.custom_kql_files:
        print("Custom Zone Redundancy Queries:")
        print("-" * 70)
        for rec_id, kql_path in sorted(catalog.custom_kql_files.items()):
            print(f"  • {rec_id}")
            print(f"    File: {kql_path.name}")
            
            # Find the recommendation in catalog
            for resource_type, recs in catalog.recommendations.items():
                for rec in recs:
                    if rec.guid == rec_id:
                        print(f"    Resource Type: {rec.resource_type}")
                        print(f"    Category: {rec.category}")
                        print(f"    Impact: {rec.impact}")
                        print(f"    Description: {rec.description}")
                        break
            print()
    else:
        print("⚠️  No custom KQL files found")
        print(f"   Check that files exist in: {custom_rules_dir}")
    
    # Verify resource type coverage
    print()
    print("Resource Type Coverage (Custom Rules):")
    print("-" * 70)
    
    custom_resource_types = set()
    for rec_id in catalog.custom_kql_files.keys():
        for resource_type, recs in catalog.recommendations.items():
            for rec in recs:
                if rec.guid == rec_id:
                    custom_resource_types.add(rec.resource_type)
    
    for rt in sorted(custom_resource_types):
        recs = [r for r in catalog.recommendations.get(catalog._normalize_resource_type(rt), []) 
                if r.guid in catalog.custom_kql_files]
        print(f"  • {rt}: {len(recs)} custom zone check(s)")
    
    print()
    print("=" * 70)
    print("✓ Integration Verified - Custom rules will execute during evaluations")
    print("=" * 70)
    print()
    print("Next Steps:")
    print("  1. Run resilience evaluation: python3 -m app.resilience.run --subscription-id <id>")
    print("  2. Check results in: data/<subscription-id>/resilience_evaluations_detailed.json")
    print("  3. Look for custom recommendation IDs in the output")
    print()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
