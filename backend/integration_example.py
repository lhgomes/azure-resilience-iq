"""
Integration example: How to use multi-source signals in the graph building pipeline.
This shows how to incorporate the new signal aggregation into your existing code.
"""

import json
from typing import Dict, List, Any
from app.relationships.multi_source import MultiSourceAggregator
from app.relationships.signal_types import SignalType


def build_graph_with_multi_source_signals(
    resources_by_id: Dict[str, Dict[str, Any]],
    appinsights_data: List[Dict[str, Any]] | None = None,
    flow_logs_data: List[Dict[str, Any]] | None = None
) -> Dict[str, Any]:
    """
    Build graph with multi-source signal enrichment.
    
    Example integration pattern for app/graph/builder.py or similar.
    """
    
    # 1. Initialize aggregator
    aggregator = MultiSourceAggregator(resources_by_id)
    
    # 2. Extract all signals
    unified_edges = aggregator.extract_all_signals(
        appinsights_data=appinsights_data,
        flow_logs_data=flow_logs_data
    )
    
    # 3. Convert to graph format
    graph = {
        'nodes': list(resources_by_id.keys()),
        'edges': []
    }
    
    for unified_edge in unified_edges:
        # Basic edge properties
        edge = {
            'id': f"{unified_edge.from_id}|{unified_edge.to_id}",
            'from_id': unified_edge.from_id,
            'to_id': unified_edge.to_id,
            'relationship': unified_edge.relationship,
            'confidence': round(unified_edge.confidence, 3),
            'origin': unified_edge.origin,
            'timestamp': unified_edge.timestamp
        }
        
        # Add signal information
        edge['signals'] = {
            'types': [s['type'] for s in unified_edge.signals],
            'count': len(unified_edge.signals),
            'details': unified_edge.signals
        }
        
        # Add evidence
        edge['evidence'] = unified_edge.evidence
        
        # Add derived fields useful for UI/filtering
        edge['signal_types'] = set(s['type'] for s in unified_edge.signals)
        edge['has_runtime_signals'] = any(
            s['type'] in [
                SignalType.FLOW_LOG_OBSERVED.value,
                SignalType.APP_INSIGHTS.value
            ]
            for s in unified_edge.signals
        )
        edge['has_config_signals'] = any(
            s['type'] in [
                SignalType.CONN_STRING.value,
                SignalType.APP_CONFIG.value
            ]
            for s in unified_edge.signals
        )
        
        graph['edges'].append(edge)
    
    return graph


def enrich_existing_edges_with_signals(
    edges: List[Dict[str, Any]],
    unified_edges_map: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Enhance existing edges with signal information.
    Use if you already have an edge list and want to add signals.
    
    Args:
        edges: Existing edge list
        unified_edges_map: Dict keyed by "from_id|to_id"
    
    Returns:
        Enhanced edges with signals
    """
    enriched = []
    
    for edge in edges:
        key = f"{edge['from_id']}|{edge['to_id']}"
        
        # Keep original edge properties
        enriched_edge = {**edge}
        
        # Add signals if available
        if key in unified_edges_map:
            unified = unified_edges_map[key]
            enriched_edge['signals'] = unified['signals']
            enriched_edge['confidence'] = unified['confidence']
            enriched_edge['evidence'] = unified['evidence']
        else:
            # Mark as no signals found
            enriched_edge['signals'] = {'types': [], 'count': 0, 'details': []}
            enriched_edge['confidence'] = 0  # No confidence without signals
            enriched_edge['evidence'] = []
        
        enriched.append(enriched_edge)
    
    return enriched


def filter_edges_by_confidence(
    edges: List[Dict[str, Any]],
    min_confidence: float = 0.7
) -> List[Dict[str, Any]]:
    """
    Filter edges by confidence threshold.
    Useful for high-fidelity views.
    """
    return [e for e in edges if e.get('confidence', 0) >= min_confidence]


def group_edges_by_signals(
    edges: List[Dict[str, Any]]
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Group edges by signal types.
    Useful for analyzing which detection methods found what.
    """
    grouped = {
        'arm_declared': [],
        'static_topology': [],
        'config_based': [],
        'runtime_observed': [],
        'multi_source': []
    }
    
    for edge in edges:
        signal_types = set(edge.get('signals', {}).get('types', []))
        signal_count = len(signal_types)
        
        if signal_count == 0:
            continue
        elif signal_count == 1:
            # Single signal - categorize
            signal_type = list(signal_types)[0]
            
            if signal_type == SignalType.ARM_DECLARED.value:
                grouped['arm_declared'].append(edge)
            elif signal_type in [
                SignalType.VNET_COUPLING.value,
                SignalType.SUBNET_ROUTING.value,
                SignalType.ROUTE_TABLE.value
            ]:
                grouped['static_topology'].append(edge)
            elif signal_type in [
                SignalType.CONN_STRING.value,
                SignalType.APP_CONFIG.value
            ]:
                grouped['config_based'].append(edge)
            elif signal_type in [
                SignalType.FLOW_LOG_OBSERVED.value,
                SignalType.APP_INSIGHTS.value
            ]:
                grouped['runtime_observed'].append(edge)
        else:
            # Multiple signals
            grouped['multi_source'].append(edge)
    
    return grouped


def generate_edge_report(edges: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Generate summary report of edges and signal coverage.
    """
    report = {
        'total_edges': len(edges),
        'edges_with_signals': 0,
        'average_confidence': 0,
        'signal_distribution': {},
        'confidence_distribution': {
            'high': 0,    # >= 0.85
            'medium': 0,  # 0.65-0.85
            'low': 0      # < 0.65
        },
        'multi_signal_edges': 0,
        'runtime_signal_edges': 0,
        'config_signal_edges': 0
    }
    
    total_confidence = 0
    edges_with_signals = 0
    
    for edge in edges:
        confidence = edge.get('confidence', 0)
        signals = edge.get('signals', {}).get('types', [])
        signal_count = len(signals)
        
        if signal_count > 0:
            edges_with_signals += 1
            total_confidence += confidence
            
            # Confidence distribution
            if confidence >= 0.85:
                report['confidence_distribution']['high'] += 1
            elif confidence >= 0.65:
                report['confidence_distribution']['medium'] += 1
            else:
                report['confidence_distribution']['low'] += 1
            
            # Signal type counts
            for signal in signals:
                report['signal_distribution'][signal] = report['signal_distribution'].get(signal, 0) + 1
            
            # Multi-signal edges
            if signal_count > 1:
                report['multi_signal_edges'] += 1
            
            # Runtime signals
            if edge.get('has_runtime_signals'):
                report['runtime_signal_edges'] += 1
            
            # Config signals
            if edge.get('has_config_signals'):
                report['config_signal_edges'] += 1
    
    report['edges_with_signals'] = edges_with_signals
    if edges_with_signals > 0:
        report['average_confidence'] = round(total_confidence / edges_with_signals, 3)
    
    return report


# Example API response structure
def format_graph_for_api(graph: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format graph with signals for REST API response.
    """
    return {
        'metadata': {
            'version': '2.0',
            'includes_signals': True,
            'timestamp': __import__('datetime').datetime.utcnow().isoformat()
        },
        'nodes': graph['nodes'],
        'edges': graph['edges'],
        'statistics': generate_edge_report(graph['edges']),
        'signals_available': [s.value for s in SignalType]
    }


if __name__ == '__main__':
    # Example usage
    print("""
    Integration Example: Multi-Source Signals
    
    Usage:
    
    1. In your graph builder:
    
        from app.relationships.multi_source import MultiSourceAggregator
        from integration_example import build_graph_with_multi_source_signals
        
        aggregator = MultiSourceAggregator(resources_by_id)
        unified_edges = aggregator.extract_all_signals()
        
        graph = build_graph_with_multi_source_signals(resources_by_id)
    
    2. For API response:
    
        from integration_example import format_graph_for_api
        
        response = format_graph_for_api(graph)
        return response
    
    3. For filtering:
    
        from integration_example import filter_edges_by_confidence
        
        high_confidence_edges = filter_edges_by_confidence(edges, min_confidence=0.85)
    
    4. For analysis:
    
        from integration_example import group_edges_by_signals, generate_edge_report
        
        report = generate_edge_report(edges)
        print(f"Total edges: {report['total_edges']}")
        print(f"With signals: {report['edges_with_signals']}")
        print(f"Avg confidence: {report['average_confidence']}")
    """)
