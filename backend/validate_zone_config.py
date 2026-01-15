#!/usr/bin/env python3
"""
Zone Support Configuration Validator

This script helps maintain the zone_support.yaml file by:
1. Validating the YAML syntax
2. Checking for duplicates across categories
3. Identifying resource types in your subscription not yet categorized
4. Providing statistics about zone support coverage

Usage:
    python validate_zone_config.py
    python validate_zone_config.py --check-subscription <subscription-id>
    python validate_zone_config.py --add-type <resource-type> --category <category>
"""

import sys
import yaml
import json
from pathlib import Path
from typing import Set, Dict, List
from collections import defaultdict

# Add backend to path
backend_dir = Path(__file__).parent
sys.path.insert(0, str(backend_dir))

config_path = backend_dir / "config" / "zone_support.yaml"


def load_config() -> Dict:
    """Load the zone support configuration."""
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def validate_config(config: Dict) -> List[str]:
    """
    Validate the configuration file.
    
    Returns:
        List of validation errors (empty if valid)
    """
    errors = []
    
    # Check required sections
    required_sections = [
        "zone_enabled_regions",
        "no_zone_support", 
        "zone_redundant_by_default", 
        "zone_support_available"
    ]
    for section in required_sections:
        if section not in config:
            errors.append(f"Missing required section: {section}")
        elif not isinstance(config[section], list):
            errors.append(f"Section '{section}' must be a list")
    
    # Check for duplicates across categories
    all_types = []
    for section in required_sections:
        if section in config:
            all_types.extend(config[section])
    
    seen = set()
    duplicates = set()
    for rt in all_types:
        rt_lower = rt.lower()
        if rt_lower in seen:
            duplicates.add(rt)
        seen.add(rt_lower)
    
    if duplicates:
        errors.append(f"Duplicate resource types found: {', '.join(sorted(duplicates))}")
    
    # Validate case consistency (all should be lowercase)
    for section in required_sections:
        if section in config:
            for rt in config[section]:
                if rt != rt.lower():
                    errors.append(f"Resource type not lowercase: {rt} (should be {rt.lower()})")
    
    return errors


def get_all_categorized_types(config: Dict) -> Set[str]:
    """Get all resource types that are already categorized."""
    all_types = set()
    sections = ["no_zone_support", "zone_redundant_by_default", "zone_support_available"]
    
    for section in sections:
        if section in config:
            all_types.update(rt.lower() for rt in config[section])
    
    return all_types


def check_subscription_coverage(subscription_id: str, config: Dict):
    """
    Check which resource types in a subscription are not yet categorized.
    
    Args:
        subscription_id: Azure subscription ID
        config: Zone support configuration
    """
    # Load resources from subscription
    resources_file = backend_dir / "data" / subscription_id / "resources.json"
    
    if not resources_file.exists():
        print(f"ERROR: Resources file not found: {resources_file}")
        print(f"Run: python -m app.collector.run --subscription-id {subscription_id}")
        return
    
    with open(resources_file, 'r') as f:
        resources_data = json.load(f)
    
    # Handle new format
    if isinstance(resources_data, dict) and "resources" in resources_data:
        resources = resources_data["resources"]
    else:
        resources = resources_data
    
    # Get all resource types in subscription
    subscription_types = set()
    type_counts = defaultdict(int)
    
    for resource in resources:
        rt = resource.get("type", "").lower()
        if rt:
            subscription_types.add(rt)
            type_counts[rt] += 1
    
    # Get categorized types
    categorized = get_all_categorized_types(config)
    
    # Find uncategorized types
    uncategorized = subscription_types - categorized
    
    # Print results
    print("=" * 80)
    print(f"ZONE SUPPORT COVERAGE ANALYSIS")
    print(f"Subscription: {subscription_id}")
    print("=" * 80)
    print(f"\nTotal unique resource types in subscription: {len(subscription_types)}")
    print(f"Already categorized: {len(subscription_types - uncategorized)}")
    print(f"Not yet categorized: {len(uncategorized)}")
    print(f"\nCoverage: {((len(subscription_types - uncategorized) / len(subscription_types)) * 100):.1f}%")
    
    if uncategorized:
        print(f"\n{'=' * 80}")
        print("UNCATEGORIZED RESOURCE TYPES")
        print("=" * 80)
        print("\nThe following resource types need to be categorized:")
        print("(Add them to config/zone_support.yaml in the appropriate section)\n")
        
        # Sort by count (most common first)
        sorted_types = sorted(uncategorized, key=lambda x: type_counts[x], reverse=True)
        
        for rt in sorted_types:
            count = type_counts[rt]
            print(f"  - {rt:<60} ({count} instance{'s' if count > 1 else ''})")
        
        print("\n" + "=" * 80)
        print("SUGGESTED CATEGORIZATION")
        print("=" * 80)
        print("\nReview Azure documentation to categorize each type:")
        print("• https://learn.microsoft.com/azure/reliability/availability-zones-service-support")
        print("\nCategories:")
        print("  - no_zone_support: Resource type doesn't support availability zones")
        print("  - zone_redundant_by_default: Automatically zone-redundant")
        print("  - zone_support_available: Can be configured for zones")
    else:
        print("\n✓ All resource types in this subscription are categorized!")


def print_statistics(config: Dict):
    """Print statistics about the configuration."""
    print("=" * 80)
    print("ZONE SUPPORT CONFIGURATION STATISTICS")
    print("=" * 80)
    
    no_zone = len(config.get("no_zone_support", []))
    redundant = len(config.get("zone_redundant_by_default", []))
    available = len(config.get("zone_support_available", []))
    total = no_zone + redundant + available
    
    print(f"\nTotal resource types categorized: {total}")
    print(f"\n  No zone support (N/A):       {no_zone:4d} ({(no_zone/total*100):.1f}%)")
    print(f"  Zone-redundant by default:   {redundant:4d} ({(redundant/total*100):.1f}%)")
    print(f"  Zone support available:      {available:4d} ({(available/total*100):.1f}%)")
    
    print(f"\nSKU patterns: {len(config.get('zone_redundant_sku_patterns', []))}")
    print(f"Property patterns: {len(config.get('zone_redundant_properties', []))}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Validate and manage zone support configuration"
    )
    parser.add_argument(
        "--check-subscription",
        metavar="SUBSCRIPTION_ID",
        help="Check coverage for a specific subscription"
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show configuration statistics"
    )
    
    args = parser.parse_args()
    
    # Load and validate config
    try:
        config = load_config()
        print(f"✓ Configuration loaded from: {config_path}")
    except Exception as e:
        print(f"✗ ERROR loading configuration: {e}")
        return 1
    
    # Validate
    errors = validate_config(config)
    if errors:
        print(f"\n✗ VALIDATION ERRORS ({len(errors)}):")
        for error in errors:
            print(f"  • {error}")
        return 1
    else:
        print("✓ Configuration is valid")
    
    # Show statistics
    if args.stats or not args.check_subscription:
        print()
        print_statistics(config)
    
    # Check subscription coverage
    if args.check_subscription:
        print()
        check_subscription_coverage(args.check_subscription, config)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
