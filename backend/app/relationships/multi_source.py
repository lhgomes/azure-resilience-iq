"""
Multi-source signal aggregator for dependency mapping.
Combines signals from all detection methods and produces unified edges with confidence scores.
"""

from typing import Any, Dict, List, Tuple
from dataclasses import dataclass
from datetime import datetime

from .signal_types import SignalType, SignalSource, calculate_aggregated_confidence
from .extract_networking import extract_networking_relationships
from .extract_aks import extract_aks_relationships
from .extract_private_endpoints import extract_private_endpoint_relationships
from .extract_appinsights import extract_appinsights_signals
from .extract_connections import extract_connection_string_signals
from .extract_dns import extract_dns_signals
from .utils import norm_id


@dataclass
class UnifiedEdge:
    """Edge with multi-source signal aggregation"""
    from_id: str
    to_id: str
    relationship: str
    signals: List[Dict[str, Any]]  # List of signal metadata
    confidence: float
    evidence: List[Dict[str, Any]]  # Supporting data from signals
    origin: str = "multi_source"
    timestamp: str = None
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + 'Z'


class MultiSourceAggregator:
    """Aggregates dependency signals from all detection methods"""
    
    def __init__(self, resources_by_id: Dict[str, Dict[str, Any]]):
        self.resources_by_id = resources_by_id
        self.edges_by_key: Dict[str, UnifiedEdge] = {}
    
    def extract_all_signals(
        self,
        appinsights_data: List[Dict[str, Any]] | None = None,
        flow_logs_data: List[Dict[str, Any]] | None = None
    ) -> List[UnifiedEdge]:
        """
        Extract signals from all sources and aggregate into unified edges.
        
        Args:
            appinsights_data: Application Insights query results
            flow_logs_data: Flow Logs query results
        
        Returns:
            List of unified edges with aggregated signals and confidence
        """
        
        # Extract from static/declarative sources
        self._extract_arm_signals()
        self._extract_networking_signals()
        self._extract_private_endpoint_signals()
        self._extract_aks_signals()
        self._extract_nsg_udr_signals()
        
        # Extract from configuration sources
        self._extract_connection_string_signals()
        
        # Extract from DNS sources
        self._extract_dns_signals()
        
        # Extract from runtime sources
        if appinsights_data:
            self._extract_appinsights_signals(appinsights_data)
        
        if flow_logs_data:
            self._extract_flow_logs_signals(flow_logs_data)
        
        # Return aggregated edges
        return list(self.edges_by_key.values())
    
    def _extract_arm_signals(self):
        """Extract signals from ARM-declared relationships"""
        # This includes explicit references in properties
        for rid, resource in self.resources_by_id.items():
            props = resource.get('properties') or {}
            
            # e.g., reference to another resource in properties
            for prop_key, prop_value in props.items():
                if isinstance(prop_value, dict) and 'id' in prop_value:
                    target_id = norm_id(prop_value['id'])
                    if target_id in self.resources_by_id:
                        self._add_signal(
                            from_id=rid,
                            to_id=target_id,
                            relationship=prop_key,
                            signal=SignalSource(
                                type=SignalType.ARM_DECLARED,
                                confidence=0.98,
                                evidence={'property': prop_key},
                                timestamp=datetime.utcnow().isoformat() + 'Z',
                                source_resource='ARM'
                            )
                        )
    
    def _extract_networking_signals(self):
        """Extract signals from networking topology"""
        edges, _ = extract_networking_relationships(self.resources_by_id)
        
        for from_id, to_id, relationship, _, _, evidence_list in edges:
            self._add_signal(
                from_id=from_id,
                to_id=to_id,
                relationship=relationship,
                signal=SignalSource(
                    type=SignalType.VNET_COUPLING,
                    confidence=0.70,
                    evidence={'topology': relationship, 'evidence': evidence_list},
                    timestamp=datetime.utcnow().isoformat() + 'Z',
                    source_resource='NetworkTopology'
                )
            )
    
    def _extract_private_endpoint_signals(self):
        """Extract signals from Private Endpoint mappings"""
        edges, _ = extract_private_endpoint_relationships(self.resources_by_id)
        
        for from_id, to_id, relationship, _, _, evidence_list in edges:
            self._add_signal(
                from_id=from_id,
                to_id=to_id,
                relationship=relationship,
                signal=SignalSource(
                    type=SignalType.PRIVATE_ENDPOINT,
                    confidence=0.96,
                    evidence={'endpoint_mapping': evidence_list},
                    timestamp=datetime.utcnow().isoformat() + 'Z',
                    source_resource='PrivateEndpoint'
                )
            )
    
    def _extract_aks_signals(self):
        """Extract signals from AKS relationships"""
        edges = extract_aks_relationships(self.resources_by_id)
        
        for from_id, to_id, relationship, _, _, evidence_list in edges:
            self._add_signal(
                from_id=from_id,
                to_id=to_id,
                relationship=relationship,
                signal=SignalSource(
                    type=SignalType.SUBNET_ROUTING,
                    confidence=0.85,
                    evidence={'aks_mapping': evidence_list},
                    timestamp=datetime.utcnow().isoformat() + 'Z',
                    source_resource='AKS'
                )
            )
    
    def _extract_nsg_udr_signals(self):
        """Extract signals from NSG and UDR analysis"""
        # This is a basic implementation - would need more sophisticated parsing
        for rid, resource in self.resources_by_id.items():
            rtype = (resource.get('type') or '').lower()
            props = resource.get('properties') or {}
            
            # NSG rules
            if 'microsoft.network/networksecuritygroups' in rtype:
                security_rules = props.get('securityRules') or []
                for rule in security_rules:
                    if rule.get('properties', {}).get('access') == 'Allow':
                        # Rule allows traffic - mark as possible dependency
                        destination = rule.get('properties', {}).get('destinationAddressPrefix')
                        if destination and destination != '*':
                            # Try to match destination to resource
                            target_id = self._find_resource_by_subnet(destination)
                            if target_id:
                                self._add_signal(
                                    from_id=rid,
                                    to_id=target_id,
                                    relationship='nsg_allows_traffic',
                                    signal=SignalSource(
                                        type=SignalType.NSG_RULE,
                                        confidence=0.68,
                                        evidence={'rule': rule.get('name'), 'destination': destination},
                                        timestamp=datetime.utcnow().isoformat() + 'Z',
                                        source_resource='NSG'
                                    )
                                )
    
    def _extract_connection_string_signals(self):
        """Extract signals from connection strings"""
        edges = extract_connection_string_signals(self.resources_by_id)
        
        for from_id, to_id, signals in edges:
            for signal in signals:
                self._add_signal(
                    from_id=from_id,
                    to_id=to_id,
                    relationship='references',
                    signal=signal
                )
    
    def _extract_dns_signals(self):
        """Extract signals from DNS configuration"""
        edges = extract_dns_signals(self.resources_by_id)
        
        for from_id, to_id, signals in edges:
            for signal in signals:
                self._add_signal(
                    from_id=from_id,
                    to_id=to_id,
                    relationship='resolves_to',
                    signal=signal
                )
    
    def _extract_appinsights_signals(self, appinsights_data: List[Dict[str, Any]]):
        """Extract signals from Application Insights"""
        edges = extract_appinsights_signals(self.resources_by_id, appinsights_data)
        
        for from_id, to_id, signals in edges:
            for signal in signals:
                self._add_signal(
                    from_id=from_id,
                    to_id=to_id,
                    relationship='observes_dependency_to',
                    signal=signal
                )
    
    def _extract_flow_logs_signals(self, flow_logs_data: List[Dict[str, Any]]):
        """Extract signals from Flow Logs"""
        for flow in flow_logs_data:
            source_id = self._find_resource_by_private_ip(flow.get('source_ip'))
            target_id = self._find_resource_by_private_ip(flow.get('dest_ip'))
            
            if source_id and target_id:
                self._add_signal(
                    from_id=source_id,
                    to_id=target_id,
                    relationship=f"flow_observed_{flow.get('protocol', 'unknown').lower()}",
                    signal=SignalSource(
                        type=SignalType.FLOW_LOG_OBSERVED,
                        confidence=0.92,
                        evidence={
                            'protocol': flow.get('protocol'),
                            'port': flow.get('dest_port'),
                            'connection_count': flow.get('connection_count'),
                            'bytes_transferred': flow.get('bytes_transferred')
                        },
                        timestamp=datetime.utcnow().isoformat() + 'Z',
                        source_resource='FlowLogs'
                    )
                )
    
    def _add_signal(
        self,
        from_id: str,
        to_id: str,
        relationship: str,
        signal: SignalSource
    ):
        """Add a signal to an edge, aggregating if edge already exists"""
        key = f"{from_id}|{to_id}"
        
        if key not in self.edges_by_key:
            # Create new edge
            self.edges_by_key[key] = UnifiedEdge(
                from_id=from_id,
                to_id=to_id,
                relationship=relationship,
                signals=[signal],
                confidence=signal['confidence'],
                evidence=[signal['evidence']]
            )
        else:
            # Aggregate signals
            edge = self.edges_by_key[key]
            edge.signals.append(signal)
            edge.evidence.append(signal['evidence'])
            
            # Recalculate confidence
            signal_types = [SignalType(s['type']) for s in edge.signals]
            edge.confidence = calculate_aggregated_confidence(signal_types)
    
    def _find_resource_by_subnet(self, subnet_prefix: str) -> str | None:
        """Find resource by CIDR subnet"""
        for rid, r in self.resources_by_id.items():
            rtype = (r.get('type') or '').lower()
            if 'microsoft.network/virtualnetworks/subnets' in rtype:
                props = r.get('properties') or {}
                if props.get('addressPrefix') == subnet_prefix:
                    return rid
        return None
    
    def _find_resource_by_private_ip(self, private_ip: str) -> str | None:
        """Find resource by private IP"""
        for rid, r in self.resources_by_id.items():
            rtype = (r.get('type') or '').lower()
            props = r.get('properties') or {}
            
            if 'microsoft.network/networkinterfaces' in rtype:
                ip_configs = props.get('ipConfigurations') or []
                for ipcfg in ip_configs:
                    if ipcfg.get('properties', {}).get('privateIPAddress') == private_ip:
                        # Return the parent (VM, App Service, etc.)
                        return ipcfg.get('properties', {}).get('primary', rid)
        
        return None
