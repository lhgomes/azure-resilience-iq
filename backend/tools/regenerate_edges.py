#!/usr/bin/env python3
"""
Regenerate edges.json from the updated resources.json
"""

import json
import sys
from pathlib import Path

# Add backend to path
backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))

from app.relationships.multi_source import MultiSourceAggregator
from app.graph.builder import edge_id


def regenerate_edges(subscription_id: str):
    """Regenerate edges from resources.json"""
    
    data_dir = backend_dir / "data" / subscription_id
    resources_file = data_dir / "resources.json"
    edges_file = data_dir / "edges.json"
    
    if not resources_file.exists():
        print(f"❌ Error: {resources_file} does not exist")
        return False
    
    print(f"📂 Loading {resources_file}")
    with open(resources_file, 'r') as f:
        data = json.load(f)
    
    resources = data.get('resources', [])
    role_assignments = data.get('role_assignments', [])
    subscription_name = data.get('subscription_name', subscription_id)
    
    print(f"✓ Loaded {len(resources)} resources")
    print(f"✓ Subscription: {subscription_name}")
    
    # Build resources_by_id lookup
    resources_by_id = {r['id']: r for r in resources}
    
    # Debug: Check if VMs have backend_pool_ids
    vms = [r for r in resources if r.get('type', '').lower() == 'microsoft.compute/virtualmachines']
    print(f"\n🔍 Found {len(vms)} VMs")
    for vm in vms[:3]:  # Show first 3
        vm_name = vm.get('name', 'unknown')
        backend_pools = vm.get('properties', {}).get('backend_pool_ids', [])
        print(f"   {vm_name}: backend_pool_ids = {backend_pools}")
    
    # Extract multi-source signals
    print("\n📊 Extracting multi-source signals...")
    aggregator = MultiSourceAggregator(resources_by_id, role_assignments)
    
    unified_edges = aggregator.extract_all_signals(
        appinsights_data=None,
        flow_logs_data=None
    )
    
    print(f"✓ Extracted {len(unified_edges)} unified edges")
    
    # Debug: Show all edge relationships
    relationship_types = {}
    for e in unified_edges:
        rel = e.relationship
        relationship_types[rel] = relationship_types.get(rel, 0) + 1
    
    print(f"\n📋 Edge relationships:")
    for rel, count in sorted(relationship_types.items()):
        print(f"   {rel}: {count}")
    
    # Filter for Load Balancer connections
    lb_edges = [e for e in unified_edges if 
                'loadbalancer' in e.relationship.lower() or 
                'backend_pool' in e.relationship.lower() or
                '/loadbalancers/' in e.source.lower() or 
                '/loadbalancers/' in e.target.lower()]
    print(f"\n✓ Found {len(lb_edges)} Load Balancer related edges")
    
    # Show some LB edges
    if lb_edges:
        print("\n🔗 Sample Load Balancer connections:")
        for edge in lb_edges[:5]:
            source_name = edge.source.split('/')[-1]
            target_name = edge.target.split('/')[-1]
            print(f"   {source_name} → {target_name} ({edge.relationship})")
    
    # Format output
    edges_output = [
        {
            'id': edge_id(edge.source, edge.target, edge.relationship),
            'source': edge.source,
            'target': edge.target,
            'relationship': edge.relationship,
            'signals': [s['type'] for s in edge.signals],
            'signal_details': edge.signals,
            'confidence': round(edge.confidence, 3),
            'evidence': edge.evidence,
            'origin': edge.origin,
            'timestamp': edge.timestamp
        }
        for edge in unified_edges
    ]
    
    # Save edges
    edges_output_data = {
        "subscription_id": subscription_id,
        "subscription_name": subscription_name,
        "edges": edges_output
    }
    
    print(f"\n💾 Saving {len(edges_output)} edges to {edges_file}")
    with open(edges_file, 'w') as f:
        json.dump(edges_output_data, f, indent=2)
    
    print(f"\n✅ Successfully regenerated edges.json!")
    
    return True


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Regenerate edges.json from resources.json")
    parser.add_argument("--subscription-id", required=True, help="Subscription ID")
    
    args = parser.parse_args()
    
    success = regenerate_edges(args.subscription_id)
    sys.exit(0 if success else 1)
