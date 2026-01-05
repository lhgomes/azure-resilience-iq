"""
Example usage of the multi-source aggregator.
Demonstrates how to use the new signal system.
"""

import json
from pathlib import Path
from app.relationships.multi_source import MultiSourceAggregator
from app.config import COLLECTOR_RESOURCES_PATH


def example_basic_aggregation():
    """Example: Basic aggregation without runtime data"""
    print("📊 Example 1: Basic Multi-Source Aggregation")
    print("=" * 60)
    
    if not COLLECTOR_RESOURCES_PATH.exists():
        print("❌ No collector output found. Run collector first:")
        print("   python -m app.collector.run --subscription-id <id>")
        return
    
    # Load resources
    with open(COLLECTOR_RESOURCES_PATH) as f:
        resources = json.load(f)
    
    resources_by_id = {r['id']: r for r in resources}
    
    # Run aggregator
    aggregator = MultiSourceAggregator(resources_by_id)
    edges = aggregator.extract_all_signals()
    
    print(f"\n✔ Extracted {len(edges)} unified edges")
    
    # Show top edges by confidence
    sorted_edges = sorted(edges, key=lambda e: e.confidence, reverse=True)
    
    print("\nTop 10 edges by confidence:")
    for i, edge in enumerate(sorted_edges[:10], 1):
        print(f"\n{i}. {edge.from_id.split('/')[-1]} → {edge.to_id.split('/')[-1]}")
        print(f"   Confidence: {edge.confidence:.3f}")
        print(f"   Signals: {', '.join(s['type'] for s in edge.signals)}")
        print(f"   Count: {len(edge.signals)} signals")


def example_with_appinsights_data():
    """Example: Aggregation with Application Insights data"""
    print("\n\n📊 Example 2: Aggregation with Application Insights")
    print("=" * 60)
    
    if not COLLECTOR_RESOURCES_PATH.exists():
        print("❌ No collector output found")
        return
    
    with open(COLLECTOR_RESOURCES_PATH) as f:
        resources = json.load(f)
    
    resources_by_id = {r['id']: r for r in resources}
    
    # Simulate App Insights data
    mock_appinsights = [
        {
            'source': 'app-service-frontend',
            'target': 'sql-server-1',
            'protocol': 'TDS',
            'avg_duration_ms': 45.2,
            'call_count': 15234,
            'failure_rate': 0.02
        },
        {
            'source': 'app-service-api',
            'target': 'cosmos-db',
            'protocol': 'HTTPS',
            'avg_duration_ms': 120.5,
            'call_count': 8900,
            'failure_rate': 0.01
        }
    ]
    
    aggregator = MultiSourceAggregator(resources_by_id)
    edges = aggregator.extract_all_signals(appinsights_data=mock_appinsights)
    
    # Show App Insights signals
    appinsights_edges = [e for e in edges if any(
        s['type'] == 'ApplicationInsights' for s in e.signals
    )]
    
    print(f"\n✔ Found {len(appinsights_edges)} edges with App Insights signals")
    for edge in appinsights_edges[:5]:
        print(f"\n{edge.from_id.split('/')[-1]} → {edge.to_id.split('/')[-1]}")
        print(f"  Signals: {', '.join(s['type'] for s in edge.signals)}")
        print(f"  Evidence: {json.dumps(edge.evidence, indent=4)}")


def example_confidence_aggregation():
    """Example: How confidence is aggregated"""
    print("\n\n📊 Example 3: Confidence Aggregation")
    print("=" * 60)
    
    from app.relationships.signal_types import (
        SignalType, 
        calculate_aggregated_confidence,
        SIGNAL_CONFIDENCE
    )
    
    # Single signals
    print("\nSingle Signal Confidence:")
    for signal_type in [
        SignalType.ARM_DECLARED,
        SignalType.FLOW_LOG_OBSERVED,
        SignalType.PRIVATE_ENDPOINT,
        SignalType.CONN_STRING
    ]:
        conf = SIGNAL_CONFIDENCE[signal_type]
        print(f"  {signal_type}: {conf}")
    
    # Multiple signals
    print("\nAggregated Confidence (multiple signals):")
    
    test_cases = [
        ([SignalType.FLOW_LOG_OBSERVED], "Flow Logs only"),
        ([SignalType.FLOW_LOG_OBSERVED, SignalType.PRIVATE_ENDPOINT], 
         "Flow Logs + Private Endpoint"),
        ([SignalType.FLOW_LOG_OBSERVED, SignalType.PRIVATE_ENDPOINT, SignalType.CONN_STRING],
         "Flow Logs + Private Endpoint + Connection String"),
        ([SignalType.FLOW_LOG_OBSERVED, SignalType.PRIVATE_ENDPOINT, 
          SignalType.CONN_STRING, SignalType.APP_INSIGHTS, SignalType.DNS_ZONE_LINK],
         "All signals")
    ]
    
    for signals, label in test_cases:
        confidence = calculate_aggregated_confidence(signals)
        print(f"  {label}: {confidence:.3f}")


def example_export_edges():
    """Example: Export edges in desired format"""
    print("\n\n📊 Example 4: Export Edges Format")
    print("=" * 60)
    
    if not COLLECTOR_RESOURCES_PATH.exists():
        return
    
    with open(COLLECTOR_RESOURCES_PATH) as f:
        resources = json.load(f)
    
    resources_by_id = {r['id']: r for r in resources}
    aggregator = MultiSourceAggregator(resources_by_id)
    edges = aggregator.extract_all_signals()
    
    # Format as requested
    formatted = []
    for edge in edges[:3]:  # Show first 3
        formatted.append({
            "from": edge.from_id.split('/')[-1],
            "to": edge.to_id.split('/')[-1],
            "signals": [s['type'] for s in edge.signals],
            "confidence": round(edge.confidence, 3)
        })
    
    print("\nEdges in requested format:")
    print(json.dumps(formatted, indent=2))


if __name__ == '__main__':
    example_basic_aggregation()
    example_with_appinsights_data()
    example_confidence_aggregation()
    example_export_edges()
