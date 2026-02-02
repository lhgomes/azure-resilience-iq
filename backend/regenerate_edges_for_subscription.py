#!/usr/bin/env python3
"""
Regenerate edges.json for a subscription from existing resources.json.
Useful when you can't re-run the full collector but need to update relationship extraction.
"""

import json
import hashlib
import sys
from pathlib import Path
from datetime import datetime
from app.relationships.multi_source import MultiSourceAggregator
from collections import Counter

def regenerate_edges(subscription_id: str):
    data_dir = Path(f"data/{subscription_id}")
    
    print(f"📊 Regenerating edges for subscription {subscription_id}...")
    
    # Load resources
    resources_file = data_dir / "resources.json"
    if not resources_file.exists():
        print(f"❌ Resources file not found: {resources_file}")
        return False
    
    with open(resources_file) as f:
        data = json.load(f)
        resources = data.get("resources", [])
        subscription_name = data.get("subscription_name", "Unknown")
    
    print(f"✔ Loaded {len(resources)} resources")
    
    # Convert to dict
    resources_by_id = {r['id']: r for r in resources}
    
    # Extract edges
    aggregator = MultiSourceAggregator(resources_by_id)
    unified_edges = aggregator.extract_all_signals(None, None)
    
    print(f"✔ Extracted {len(unified_edges)} unified edges")
    
    # Convert to output format
    edges_output = []
    for edge in unified_edges:
        edge_key = f"{edge.source}|{edge.target}|{edge.relationship}"
        edge_id = hashlib.md5(edge_key.encode()).hexdigest()
        
        signal_details = []
        signal_types = []
        evidence_list = []
        
        for s in edge.signals:
            if isinstance(s, dict):
                signal_types.append(s.get('type', 'Unknown'))
                signal_details.append(s)
                if 'evidence' in s:
                    evidence_list.append(s['evidence'])
            else:
                signal_types.append(s.type.value)
                signal_details.append({
                    "type": s.type.value,
                    "confidence": s.confidence,
                    "evidence": s.evidence,
                    "timestamp": s.timestamp,
                    "source_resource": s.source_resource
                })
                evidence_list.append(s.evidence)
        
        edges_output.append({
            "id": edge_id,
            "source": edge.source,
            "target": edge.target,
            "relationship": edge.relationship,
            "signals": signal_types,
            "signal_details": signal_details,
            "confidence": edge.confidence,
            "evidence": evidence_list,
            "origin": "multi_source",
            "timestamp": datetime.utcnow().isoformat() + 'Z'
        })
    
    # Save edges
    output = {
        "subscription_id": subscription_id,
        "subscription_name": subscription_name,
        "edges": edges_output
    }
    
    edges_file = data_dir / "edges.json"
    with open(edges_file, 'w') as f:
        json.dump(output, f, indent=2)
    
    print(f"✔ Written {len(edges_output)} edges to {edges_file}")
    
    # Show summary
    rel_counts = Counter(e["relationship"] for e in edges_output)
    print(f"\nTop edge types:")
    for rel, count in rel_counts.most_common(15):
        print(f"  {rel:40} {count}")
    
    # Highlight LB and AppGW edges
    lb_count = sum(1 for e in edges_output if 'load_balanced' in e['relationship'])
    appgw_count = sum(1 for e in edges_output if 'routed_by_appgw' in e['relationship'])
    
    if lb_count > 0:
        print(f"\n✅ Load Balancer edges: {lb_count}")
    if appgw_count > 0:
        print(f"✅ Application Gateway edges: {appgw_count}")
    
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python regenerate_edges_for_subscription.py <subscription-id>")
        sys.exit(1)
    
    subscription_id = sys.argv[1]
    success = regenerate_edges(subscription_id)
    sys.exit(0 if success else 1)
