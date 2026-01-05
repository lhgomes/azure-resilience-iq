"""
Relationship extraction and signal aggregation modules.
"""

from .multi_source import MultiSourceAggregator, UnifiedEdge
from .signal_types import SignalType, SignalSource, calculate_aggregated_confidence

__all__ = [
    'MultiSourceAggregator',
    'UnifiedEdge',
    'SignalType',
    'SignalSource',
    'calculate_aggregated_confidence'
]
