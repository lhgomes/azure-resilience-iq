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
from .extract_access_rules import extract_database_firewall_relationships, extract_role_assignment_relationships
from .extract_appinsights import extract_appinsights_signals
from .extract_connections import extract_connection_string_signals
from .extract_dns import extract_dns_signals
from .extract_compute import extract_compute_relationships
from .utils import norm_id


@dataclass
class UnifiedEdge:
    """Edge with multi-source signal aggregation"""
    source: str  # source node ID
    target: str  # target node ID
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
    
    def __init__(self, resources_by_id: Dict[str, Dict[str, Any]], role_assignments: List[Dict[str, Any]] = None):
        self.resources_by_id = resources_by_id
        self.role_assignments = role_assignments or []
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
        self._extract_compute_signals()
        self._extract_networking_signals()
        self._extract_private_endpoint_signals()
        self._extract_aks_signals()
        self._extract_nsg_udr_signals()
        self._extract_database_firewall_signals()
        self._extract_role_assignment_signals()
        
        # Extract from configuration sources
        self._extract_connection_string_signals()
        
        # Extract from DNS sources
        self._extract_dns_signals()
        
        # Extract from runtime sources
        if appinsights_data:
            self._extract_appinsights_signals(appinsights_data)
        
        if flow_logs_data:
            self._extract_flow_logs_signals(flow_logs_data)
        
        # Deduplicate reverse edges
        self._deduplicate_reverse_edges()
        
        # Return aggregated edges
        return list(self.edges_by_key.values())
    
    def _extract_arm_signals(self):
        """Extract signals from ARM-declared relationships"""
        # This includes explicit references in properties (both dict-based and string-based)
        for rid, resource in self.resources_by_id.items():
            props = resource.get('properties') or {}
            
            for prop_key, prop_value in props.items():
                # 1. Dictionary-based references: {id: "..."}
                if isinstance(prop_value, dict) and 'id' in prop_value:
                    target_id = norm_id(prop_value['id'])
                    if target_id in self.resources_by_id:
                        self._add_signal(
                            source=rid,
                            target=target_id,
                            relationship=prop_key,
                            signal=SignalSource(
                                type=SignalType.ARM_DECLARED,
                                confidence=0.98,
                                evidence={'property': prop_key, 'type': 'dict_reference'},
                                timestamp=datetime.utcnow().isoformat() + 'Z',
                                source_resource='ARM'
                            )
                        )
                
                # 2. String-based resource ID references: "Id" or "ResourceId" properties
                # These contain full resource paths like "/subscriptions/.../providers/..."
                elif isinstance(prop_value, str) and prop_key.endswith(('Id', 'ResourceId', 'resourceId')):
                    if prop_value.startswith('/subscriptions/'):
                        target_id = norm_id(prop_value)
                        if target_id in self.resources_by_id:
                            # Use semantic relationship names based on property and target type
                            target_resource = self.resources_by_id.get(target_id, {})
                            target_type = (target_resource.get('type') or '').lower()
                            relationship = self._semantic_relationship_name(prop_key, target_type)
                            
                            self._add_signal(
                                source=rid,
                                target=target_id,
                                relationship=relationship,
                                signal=SignalSource(
                                    type=SignalType.ARM_DECLARED,
                                    confidence=0.98,
                                    evidence={'property': prop_key, 'type': 'string_reference'},
                                    timestamp=datetime.utcnow().isoformat() + 'Z',
                                    source_resource='ARM'
                                )
                            )
    
    def _semantic_relationship_name(self, prop_key: str, target_type: str) -> str:
        """
        Convert property names to semantic relationship names.
        
        Examples:
        - targetResourceId + vm -> manages_vm
        - targetResourceId + schedule -> schedules
        """
        # Map property names to semantic relationships
        semantic_map = {
            'targetresourceid': {
                'microsoft.compute/virtualmachines': 'manages_vm',
                'microsoft.compute/virtualmachinescalesets': 'manages_vmss',
                'microsoft.network/networkinterfaces': 'manages_nic',
            },
            'storageuri': {
                'microsoft.storage/storageaccounts': 'uses_storage_account',
            }
        }
        
        prop_key_lower = prop_key.lower()
        
        # Check if we have a semantic mapping for this property
        if prop_key_lower in semantic_map:
            return semantic_map[prop_key_lower].get(target_type, prop_key)
        
        # Default: return the original property name
        return prop_key
    
    def _extract_compute_signals(self):
        """Extract signals from compute resources (VMs, VMScaleSets)"""
        edges = extract_compute_relationships(self.resources_by_id)
        
        for source, target, relationship, signal_type, confidence, evidence_list in edges:
            self._add_signal(
                source=source,
                target=target,
                relationship=relationship,
                signal=SignalSource(
                    type=SignalType.ARM_DECLARED,
                    confidence=confidence,
                    evidence={'compute_resource': relationship, 'evidence': evidence_list},
                    timestamp=datetime.utcnow().isoformat() + 'Z',
                    source_resource='ComputeResource'
                )
            )
    
    def _extract_networking_signals(self):
        """Extract signals from networking topology"""
        edges, _ = extract_networking_relationships(self.resources_by_id)
        
        for source, target, relationship, _, _, evidence_list in edges:
            self._add_signal(
                source=source,
                target=target,
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
        
        for source, target, relationship, _, _, evidence_list in edges:
            self._add_signal(
                source=source,
                target=target,
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
        
        for source, target, relationship, _, _, evidence_list in edges:
            self._add_signal(
                source=source,
                target=target,
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
                            source=rid,
                            target=target_id,
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
        
        for source, target, signals in edges:
            for signal in signals:
                self._add_signal(
                    source=source,
                    target=target,
                    relationship='references',
                    signal=signal
                )
    
    def _extract_dns_signals(self):
        """Extract signals from DNS configuration"""
        edges = extract_dns_signals(self.resources_by_id)
        
        for source, target, signals in edges:
            for signal in signals:
                self._add_signal(
                    source=source,
                    target=target,
                    relationship='resolves_to',
                    signal=signal
                )
    
    def _extract_appinsights_signals(self, appinsights_data: List[Dict[str, Any]]):
        """Extract signals from Application Insights"""
        edges = extract_appinsights_signals(self.resources_by_id, appinsights_data)
        
        for source, target, signals in edges:
            for signal in signals:
                self._add_signal(
                    source=source,
                    target=target,
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
                    source=source_id,
                    target=target_id,
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
        source: str,
        target: str,
        relationship: str,
        signal: SignalSource
    ):
        """Add a signal to an edge, aggregating if edge already exists"""
        key = f"{source}|{target}"
        
        if key not in self.edges_by_key:
            # Create new edge
            self.edges_by_key[key] = UnifiedEdge(
                source=source,
                target=target,
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
    def _deduplicate_reverse_edges(self):
        """
        Remove bidirectional edges, keeping only the one with higher priority.
        
        Priority system (higher number = keep this direction):
        - Direct parent-child relationships (e.g., VM → NIC) take precedence over reverse references
        - Relationship type priority determines which direction to keep
        """
        # Define relationship type priorities (higher = preferred direction)
        # Format: (from_resource_type, to_resource_type, relationship) -> priority
        RELATIONSHIP_PRIORITIES = {
            # Compute resources
            ('microsoft.compute/virtualmachines', 'microsoft.network/networkinterfaces', 'uses_nic'): 100,
            ('microsoft.network/networkinterfaces', 'microsoft.compute/virtualmachines', 'virtualmachine'): 50,
            
            ('microsoft.compute/virtualmachines', 'microsoft.compute/disks', 'uses_os_disk'): 100,
            ('microsoft.compute/disks', 'microsoft.compute/virtualmachines', 'vm'): 50,
            
            ('microsoft.compute/virtualmachines', 'microsoft.compute/sshpublickeys', 'uses_ssh_key'): 100,
            ('microsoft.compute/sshpublickeys', 'microsoft.compute/virtualmachines', 'vm'): 50,
            
            # VNet relationships (prefer subnet -> vnet direction)
            ('microsoft.network/virtualnetworks/subnets', 'microsoft.network/virtualnetworks', 'belongs_to_vnet'): 100,
            ('microsoft.network/virtualnetworks', 'microsoft.network/virtualnetworks/subnets', 'contains_subnet'): 50,
        }
        
        # Find reverse edge pairs
        edges_to_remove = set()
        
        for key1, edge1 in list(self.edges_by_key.items()):
            if key1 in edges_to_remove:
                continue
            
            # Look for reverse edge (B → A when we have A → B)
            reverse_key = f"{edge1.target}|{edge1.source}"
            if reverse_key not in self.edges_by_key:
                continue
            
            edge2 = self.edges_by_key[reverse_key]
            
            # Get resource types
            res1 = self.resources_by_id.get(edge1.source, {})
            res2 = self.resources_by_id.get(edge1.target, {})
            type1 = (res1.get('type') or '').lower()
            type2 = (res2.get('type') or '').lower()
            
            # Lookup priorities
            priority1 = RELATIONSHIP_PRIORITIES.get(
                (type1, type2, edge1.relationship), 0
            )
            priority2 = RELATIONSHIP_PRIORITIES.get(
                (type2, type1, edge2.relationship), 0
            )
            
            # If priorities are equal, use confidence as tiebreaker
            if priority1 == priority2:
                priority1 = edge1.confidence
                priority2 = edge2.confidence
            
            # Remove the lower priority edge
            if priority1 > priority2:
                edges_to_remove.add(reverse_key)
            elif priority2 > priority1:
                edges_to_remove.add(key1)
        
        # Remove duplicates
        for key in edges_to_remove:
            self.edges_by_key.pop(key, None)
    
    def _extract_database_firewall_signals(self):
        """Extract signals from database firewall rules and VNet rules"""
        edges = extract_database_firewall_relationships(self.resources_by_id)
        
        for source, target, relationship, source_type, confidence, evidence in edges:
            self._add_signal(
                source=source,
                target=target,
                relationship=relationship,
                signal=SignalSource(
                    type=SignalType.ARM_DECLARED if source_type == "arg" else SignalType.HEURISTIC,
                    confidence=confidence,
                    evidence=evidence,
                    timestamp=datetime.utcnow().isoformat() + 'Z',
                    source_resource=source_type
                )
            )
    
    def _extract_role_assignment_signals(self):
        """Extract signals from RBAC role assignments"""
        if not self.role_assignments:
            return
        
        edges = extract_role_assignment_relationships(self.resources_by_id, self.role_assignments)
        
        for source, target, relationship, source_type, confidence, evidence in edges:
            self._add_signal(
                source=source,
                target=target,
                relationship=relationship,
                signal=SignalSource(
                    type=SignalType.ARM_DECLARED,  # RBAC is ARM-managed
                    confidence=confidence,
                    evidence=evidence,
                    timestamp=datetime.utcnow().isoformat() + 'Z',
                    source_resource=source_type
                )
            )