#!/usr/bin/env python3
"""
Post-process existing resources.json to fix backend_pool_ids mapping.
This applies the new logic that extracts backend pool IDs from NICs and maps them to VMs.
"""

import json
import sys
from pathlib import Path

# Add backend to path
backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))

from app.collector.arg import populate_backend_pool_ids


def fix_backend_pools_for_subscription(subscription_id: str):
    """Fix backend pool IDs in an existing resources.json file"""
    
    # Load existing data
    resources_file = backend_dir / "data" / subscription_id / "resources.json"
    
    if not resources_file.exists():
        print(f"❌ Error: {resources_file} does not exist")
        return False
    
    print(f"📂 Loading {resources_file}")
    with open(resources_file, 'r') as f:
        data = json.load(f)
    
    resources = data.get('resources', [])
    print(f"✓ Loaded {len(resources)} resources")
    
    # Count resources before
    vms_before = [r for r in resources if 'microsoft.compute/virtualmachines' in r.get('type', '').lower() and '/extensions' not in r.get('type', '').lower()]
    vms_with_pools_before = [r for r in vms_before if r.get('backend_pool_ids')]
    
    print(f"\n📊 Before processing:")
    print(f"   VMs: {len(vms_before)}")
    print(f"   VMs with backend_pool_ids: {len(vms_with_pools_before)}")
    
    # Show some examples before
    print(f"\n🔍 Sample before:")
    for vm in vms_before[:3]:
        print(f"   {vm['name']}: {vm.get('backend_pool_ids')}")
    
    # Apply the fix
    print(f"\n🔧 Applying backend pool ID fix...")
    populate_backend_pool_ids(resources)
    
    # Count resources after
    vms_after = [r for r in resources if 'microsoft.compute/virtualmachines' in r.get('type', '').lower() and '/extensions' not in r.get('type', '').lower()]
    vms_with_pools_after = [r for r in vms_after if r.get('backend_pool_ids')]
    
    print(f"\n📊 After processing:")
    print(f"   VMs: {len(vms_after)}")
    print(f"   VMs with backend_pool_ids: {len(vms_with_pools_after)}")
    
    # Show some examples after
    print(f"\n🔍 Sample after:")
    for vm in vms_after[:3]:
        pools = vm.get('backend_pool_ids')
        if pools:
            # Show just the last part of the pool ID for readability
            pool_names = [p.split('/')[-1] for p in pools]
            print(f"   {vm['name']}: {pool_names}")
        else:
            print(f"   {vm['name']}: {pools}")
    
    # Update the data
    data['resources'] = resources
    
    # Save back to file
    print(f"\n💾 Saving updated data to {resources_file}")
    with open(resources_file, 'w') as f:
        json.dump(data, f, indent=2)
    
    print(f"\n✅ Successfully fixed backend_pool_ids!")
    print(f"   Changed: {len(vms_with_pools_after) - len(vms_with_pools_before)} VMs now have correct backend pool references")
    
    return True


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Fix backend pool IDs in existing resources.json")
    parser.add_argument("--subscription-id", required=True, help="Subscription ID")
    
    args = parser.parse_args()
    
    success = fix_backend_pools_for_subscription(args.subscription_id)
    sys.exit(0 if success else 1)
