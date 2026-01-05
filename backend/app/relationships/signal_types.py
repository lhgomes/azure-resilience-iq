"""
Signal types for multi-source dependency detection.
Each signal represents evidence from a different detection method.
"""

from enum import Enum
from typing import TypedDict


class SignalType(str, Enum):
    """Enumeration of all detection methods"""
    # Static/Declarative (High Confidence)
    ARM_DECLARED = "ARM_Declared"
    PRIVATE_ENDPOINT = "PrivateEndpoint"
    SUBNET_ROUTING = "SubnetRouting"
    NSG_RULE = "NSGRule"
    
    # Configuration-based (Medium-High Confidence)
    CONN_STRING = "ConnectionStringMatch"
    APP_CONFIG = "AppConfigReference"
    
    # Network inference (Medium Confidence)
    VNET_COUPLING = "VNetCoupling"
    ROUTE_TABLE = "RouteTable"
    
    # Name Resolution (Medium-High Confidence)
    DNS_ZONE_LINK = "DNSZoneLink"
    PRIVATE_DNS = "PrivateDNS"
    
    # Runtime Observation (High Confidence)
    FLOW_LOG_OBSERVED = "FlowLogObserved"
    APP_INSIGHTS = "ApplicationInsights"
    NETWORK_WATCHER = "NetworkWatcher"


class SignalSource(TypedDict):
    """Metadata about a signal detection"""
    type: SignalType
    confidence: float  # 0.0 to 1.0
    evidence: dict  # Specific data supporting this signal
    timestamp: str  # When detected (ISO format)
    source_resource: str  # Which resource provided evidence


# Signal confidence scores (can be tuned)
SIGNAL_CONFIDENCE: dict[SignalType, float] = {
    # Highest confidence - directly declared
    SignalType.ARM_DECLARED: 0.98,
    SignalType.PRIVATE_ENDPOINT: 0.96,
    
    # High confidence - configuration-based
    SignalType.CONN_STRING: 0.90,
    SignalType.APP_CONFIG: 0.88,
    
    # High confidence - runtime observed
    SignalType.FLOW_LOG_OBSERVED: 0.92,
    SignalType.APP_INSIGHTS: 0.90,
    SignalType.NETWORK_WATCHER: 0.89,
    
    # Medium-High confidence - DNS resolution
    SignalType.DNS_ZONE_LINK: 0.82,
    SignalType.PRIVATE_DNS: 0.85,
    
    # Medium confidence - network topology
    SignalType.VNET_COUPLING: 0.70,
    SignalType.ROUTE_TABLE: 0.72,
    SignalType.SUBNET_ROUTING: 0.75,
    SignalType.NSG_RULE: 0.68,
}


def calculate_aggregated_confidence(signals: list[SignalType]) -> float:
    """
    Calculate confidence from multiple signals.
    More signals = higher confidence (Bayesian-style aggregation).
    """
    if not signals:
        return 0.0
    
    if len(signals) == 1:
        return SIGNAL_CONFIDENCE.get(signals[0], 0.5)
    
    # Aggregate multiple signals: highest signal + boost for additional signals
    sorted_signals = sorted(signals, key=lambda s: SIGNAL_CONFIDENCE.get(s, 0.5), reverse=True)
    
    base_confidence = SIGNAL_CONFIDENCE.get(sorted_signals[0], 0.5)
    boost_per_signal = 0.02  # Each additional signal adds 2% confidence
    boost = min((len(signals) - 1) * boost_per_signal, 0.15)  # Cap at 15% boost
    
    return min(base_confidence + boost, 1.0)
