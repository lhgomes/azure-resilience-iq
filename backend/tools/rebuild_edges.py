#!/usr/bin/env python3
"""
Script to rebuild edges with the updated relationship extraction logic.
Uses the normalize_ids script as a base but regenerates edges using the
updated extract_networking.py logic.
"""

import json
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))

from app.relationships.multi_source import MultiSourceAggregator
from app.graph.builder import edge_id

def main():
    subscription_id = "59e12ca5-d654-418e-bc74-ef6f56c92836"
    data_dir = backend_dir / "data" / subscription_id
    resources_file = data_dir / "resources.json"
    edges_file = data_dir / "edges.json"
    
    if not resources_file.exists():
        print(f"❌ Resources file not found: {resources_file}")
        sys.exit(1)
    
    print(f"📖 Loading resources from {resources_file}...")
    with open(resources_file) as f:
        resources_data = json.load(f)
    
    subscription_name = resources_data.get("subscription_name", subscription_id)
    resources = resources_data.get("resources", [])
    role_assignments = resources_data.get("role_assignments", [])
    
    # Build lookup by normalized ID
    resources_by_id = {r['id']: r for r in resources}
    
    print(f"🔗 Building edges with updated relationship extraction...")
    
    # Extract edges using the relationship aggregator with updated logic
    aggregator = MultiSourceAggregator(resources_by_id, role_assignments)
    unified_edges = aggregator.extract_all_signals(
        appinsights_data=None,
        flow_logs_data=None
    )
    
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
    
    with open(edges_file, 'w') as f:
        json.dump(edges_output_data, f, indent=2)
    
    print(f"✅ Edges rebuilt and saved to {edges_file}")
    print(f"✔ Total edges: {len(edges_output)}")
    
    # Show summary of new edges
    print(f"\n--- Edge Summary ---")
    from collections import Counter
    relationships = Counter(e['relationship'] for e in edges_output)
    for rel, count in sorted(relationships.items()):
        print(f"  {rel}: {count}")


if __name__ == "__main__":
    main()
