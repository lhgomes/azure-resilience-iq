"""
Extract dependency signals from Application Insights.
Detects application-level dependencies via HTTP, RPC, and custom calls.
"""

from typing import Any, Dict, List, Tuple
from datetime import datetime
from .signal_types import SignalType, SignalSource, SIGNAL_CONFIDENCE
from .utils import norm_id


def extract_appinsights_signals(
    resources_by_id: Dict[str, Dict[str, Any]],
    appinsights_data: List[Dict[str, Any]] | None = None
) -> List[Tuple[str, str, List[SignalSource]]]:
    """
    Extract dependency signals from Application Insights data.
    
    Args:
        resources_by_id: Indexed resources
        appinsights_data: Query results from Application Insights
                         Expected format: [{ source, target, protocol, avg_duration, call_count, ... }]
    
    Returns:
        List of (source, target, [signals]) tuples
    """
    signals_list: List[Tuple[str, str, List[SignalSource]]] = []
    
    if not appinsights_data:
        return signals_list
    
    # Build app name → resource ID map
    app_to_resource = _build_app_name_map(resources_by_id)
    
    for connection in appinsights_data:
        source_app = connection.get('source', '')
        target = connection.get('target', '')
        
        # Find source resource ID
        source_id = app_to_resource.get(source_app.lower())
        if not source_id:
            continue
        
        # Find target resource ID (could be app, db, service)
        target_id = app_to_resource.get(target.lower())
        if not target_id:
            # Try to match by resource name or hostname
            target_id = _find_resource_by_hostname(resources_by_id, target)
        
        if not target_id:
            continue
        
        # Create signal
        signal = SignalSource(
            type=SignalType.APP_INSIGHTS,
            confidence=SIGNAL_CONFIDENCE[SignalType.APP_INSIGHTS],
            evidence={
                'protocol': connection.get('protocol', 'HTTP'),
                'avg_duration_ms': connection.get('avg_duration_ms'),
                'call_count': connection.get('call_count', 1),
                'failure_rate': connection.get('failure_rate', 0),
                'observation_period': '24h'
            },
            timestamp=datetime.utcnow().isoformat() + 'Z',
            source_resource='ApplicationInsights'
        )
        
        signals_list.append((source_id, target_id, [signal]))
    
    return signals_list


def _build_app_name_map(resources_by_id: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """Map application names to resource IDs"""
    app_map = {}
    
    for rid, r in resources_by_id.items():
        rtype = (r.get('type') or '').lower()
        name = (r.get('name') or '').lower()
        
        # App Services
        if 'microsoft.web/sites' in rtype:
            app_map[name] = rid
        
        # Databases
        if 'microsoft.sql/servers/databases' in rtype or \
           'microsoft.dbforpostgresql/servers' in rtype or \
           'microsoft.dbformysql/servers' in rtype:
            app_map[name] = rid
        
        # Cosmos DB
        if 'microsoft.documentdb/databaseaccounts' in rtype:
            app_map[name] = rid
        
        # App Insights itself
        if 'microsoft.insights/components' in rtype:
            app_map[name] = rid
        
        # Azure Functions
        if 'microsoft.web/sites' in rtype and 'functionapp' in name:
            app_map[name] = rid
    
    return app_map


def _find_resource_by_hostname(resources_by_id: Dict[str, Dict[str, Any]], hostname: str) -> str | None:
    """Find resource by FQDN or hostname"""
    hostname_lower = hostname.lower()
    
    for rid, r in resources_by_id.items():
        rtype = (r.get('type') or '').lower()
        props = r.get('properties') or {}
        
        # Check default hostnames
        if 'microsoft.web/sites' in rtype:
            default_hostname = props.get('defaultHostName', '').lower()
            if default_hostname and hostname_lower in default_hostname:
                return rid
        
        # Check custom domains
        if 'microsoft.sql/servers' in rtype:
            fqdn = props.get('fullyQualifiedDomainName', '').lower()
            if fqdn and hostname_lower in fqdn:
                return rid
    
    return None
