"""
Extract dependency signals from DNS and Private DNS zones.
Detects name resolution dependencies.
"""

from typing import Any, Dict, List, Tuple, Set
from datetime import datetime
from .signal_types import SignalType, SignalSource, SIGNAL_CONFIDENCE
from .utils import norm_id, parent_id


def extract_dns_signals(
    resources_by_id: Dict[str, Dict[str, Any]]
) -> List[Tuple[str, str, List[SignalSource]]]:
    """
    Extract dependency signals from DNS and Private DNS configurations.
    
    Detects:
    - Private DNS Zone links to VNets
    - A/CNAME records pointing to resources
    - Private Endpoint DNS registration
    
    Returns:
        List of (source, target, [signals]) tuples
    """
    signals_list: List[Tuple[str, str, List[SignalSource]]] = []
    
    # Process Private DNS Zones
    for rid, resource in resources_by_id.items():
        rtype = (resource.get('type') or '').lower()
        props = resource.get('properties') or {}
        
        # microsoft.network/privatednszones
        if 'microsoft.network/privatednszones' in rtype:
            zone_name = resource.get('name', '')
            
            # Get VNets linked to this DNS zone
            virtual_network_links = props.get('virtualNetworkLinks') or []
            if isinstance(virtual_network_links, dict):
                # If it's a dict with resource references
                virtual_network_links = list(virtual_network_links.values())
            
            for vnet_link in virtual_network_links:
                if isinstance(vnet_link, dict):
                    vnet_id = vnet_link.get('id') or vnet_link.get('virtualNetworkId')
                    if vnet_id:
                        vnet_id_norm = norm_id(vnet_id)
                        signal = SignalSource(
                            type=SignalType.PRIVATE_DNS,
                            confidence=SIGNAL_CONFIDENCE[SignalType.PRIVATE_DNS],
                            evidence={
                                'dns_zone': zone_name,
                                'link_type': 'VNetLinkage'
                            },
                            timestamp=datetime.utcnow().isoformat() + 'Z',
                            source_resource='PrivateDNS'
                        )
                        signals_list.append((rid, vnet_id_norm, [signal]))
            
            # Parse A records pointing to other resources
            a_records = props.get('aRecords') or {}
            for record_name, records in a_records.items():
                if isinstance(records, list):
                    for record in records:
                        ipv4_address = record.get('ipv4Address')
                        if ipv4_address:
                            # Try to find resource by private IP
                            target_id = _find_resource_by_private_ip(resources_by_id, ipv4_address)
                            if target_id:
                                signal = SignalSource(
                                    type=SignalType.PRIVATE_DNS,
                                    confidence=SIGNAL_CONFIDENCE[SignalType.PRIVATE_DNS],
                                    evidence={
                                        'dns_zone': zone_name,
                                        'record': f'{record_name}.{zone_name}',
                                        'record_type': 'A',
                                        'ip_address': ipv4_address
                                    },
                                    timestamp=datetime.utcnow().isoformat() + 'Z',
                                    source_resource='PrivateDNS'
                                )
                                signals_list.append((target_id, rid, [signal]))
        
        # Public DNS Zones (Azure DNS)
        if 'microsoft.network/dnszones' in rtype:
            zone_name = resource.get('name', '')
            
            # A Records pointing to endpoints
            a_records = props.get('aRecords') or {}
            for record_name, records in a_records.items():
                if isinstance(records, dict):
                    ipv4 = records.get('ipv4Address')
                    if ipv4:
                        target_id = _find_resource_by_public_ip(resources_by_id, ipv4)
                        if target_id:
                            signal = SignalSource(
                                type=SignalType.DNS_ZONE_LINK,
                                confidence=SIGNAL_CONFIDENCE[SignalType.DNS_ZONE_LINK],
                                evidence={
                                    'dns_zone': zone_name,
                                    'record': f'{record_name}.{zone_name}',
                                    'record_type': 'A'
                                },
                                timestamp=datetime.utcnow().isoformat() + 'Z',
                                source_resource='DNSZone'
                            )
                            signals_list.append((target_id, rid, [signal]))
            
            # CNAME Records
            cname_records = props.get('cnameRecord') or {}
            for record_name, target_name in cname_records.items():
                # CNAMEs point to other services
                target_id = _find_resource_by_hostname(resources_by_id, target_name)
                if target_id:
                    signal = SignalSource(
                        type=SignalType.DNS_ZONE_LINK,
                        confidence=SIGNAL_CONFIDENCE[SignalType.DNS_ZONE_LINK],
                        evidence={
                            'dns_zone': zone_name,
                            'record': record_name,
                            'record_type': 'CNAME',
                            'target': target_name
                        },
                        timestamp=datetime.utcnow().isoformat() + 'Z',
                        source_resource='DNSZone'
                    )
                    signals_list.append((target_id, rid, [signal]))
    
    return signals_list


def _find_resource_by_private_ip(
    resources_by_id: Dict[str, Dict[str, Any]],
    private_ip: str
) -> str | None:
    """Find NIC or resource by private IP address"""
    for rid, r in resources_by_id.items():
        rtype = (r.get('type') or '').lower()
        props = r.get('properties') or {}
        
        # Check NICs
        if 'microsoft.network/networkinterfaces' in rtype:
            ip_configs = props.get('ipConfigurations') or []
            for ipcfg in ip_configs:
                if ipcfg.get('properties', {}).get('privateIPAddress') == private_ip:
                    return rid
        
        # Check other resources with private IPs
        if 'properties' in r:
            for key, value in props.items():
                if isinstance(value, dict) and value.get('privateIPAddress') == private_ip:
                    return rid
    
    return None


def _find_resource_by_public_ip(
    resources_by_id: Dict[str, Dict[str, Any]],
    public_ip: str
) -> str | None:
    """Find public IP resource by address"""
    for rid, r in resources_by_id.items():
        rtype = (r.get('type') or '').lower()
        props = r.get('properties') or {}
        
        if 'microsoft.network/publicipaddresses' in rtype:
            if props.get('ipAddress') == public_ip:
                return rid
    
    return None


def _find_resource_by_hostname(
    resources_by_id: Dict[str, Dict[str, Any]],
    hostname: str
) -> str | None:
    """Find resource by FQDN or hostname"""
    hostname_lower = hostname.lower().rstrip('.')
    
    for rid, r in resources_by_id.items():
        rtype = (r.get('type') or '').lower()
        props = r.get('properties') or {}
        
        # App Services
        if 'microsoft.web/sites' in rtype:
            default_hostname = props.get('defaultHostName', '').lower()
            if default_hostname and hostname_lower in default_hostname:
                return rid
        
        # SQL Servers
        if 'microsoft.sql/servers' in rtype:
            fqdn = props.get('fullyQualifiedDomainName', '').lower()
            if fqdn and hostname_lower in fqdn:
                return rid
        
        # Cosmos DB
        if 'microsoft.documentdb/databaseaccounts' in rtype:
            endpoint = props.get('documentEndpoint', '').lower()
            if endpoint and hostname_lower in endpoint:
                return rid
        
        # API Management
        if 'microsoft.apimanagement/service' in rtype:
            gateway_url = props.get('gatewayUrl', '').lower()
            if gateway_url and hostname_lower in gateway_url:
                return rid
    
    return None
