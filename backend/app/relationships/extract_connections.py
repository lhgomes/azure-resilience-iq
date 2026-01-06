"""
Extract dependency signals from connection strings and app configurations.
Detects intended dependencies from config references.
"""

from typing import Any, Dict, List, Tuple
from datetime import datetime
from .signal_types import SignalType, SignalSource, SIGNAL_CONFIDENCE
from .utils import norm_id
import re


def extract_connection_string_signals(
    resources_by_id: Dict[str, Dict[str, Any]]
) -> List[Tuple[str, str, List[SignalSource]]]:
    """
    Extract dependency signals from connection strings in app configs.
    
    Detects:
    - SQL Server connections
    - Cosmos DB connections
    - Storage Account connections
    - PostgreSQL/MySQL connections
    - Service Bus connections
    
    Returns:
        List of (source, target, [signals]) tuples
    """
    signals_list: List[Tuple[str, str, List[SignalSource]]] = []
    
    for rid, resource in resources_by_id.items():
        rtype = (resource.get('type') or '').lower()
        props = resource.get('properties') or {}
        
        # App Services and Function Apps have application settings
        if 'microsoft.web/sites' in rtype:
            config = props.get('siteConfig') or {}
            app_settings = config.get('appSettings') or []
            
            for setting in app_settings:
                name = (setting.get('name') or '').upper()
                value = setting.get('value') or ''
                
                # Skip empty or obviously non-connection values
                if not value or len(value) < 20:
                    continue
                
                # Extract connections from setting values
                connections = _parse_connection_strings(value, resources_by_id)
                for target_id, conn_type in connections:
                    signal = SignalSource(
                        type=SignalType.CONN_STRING,
                        confidence=SIGNAL_CONFIDENCE[SignalType.CONN_STRING],
                        evidence={
                            'setting_name': name,
                            'connection_type': conn_type,
                            'config_source': 'appSettings'
                        },
                        timestamp=datetime.utcnow().isoformat() + 'Z',
                        source_resource='AppConfiguration'
                    )
                    signals_list.append((rid, target_id, [signal]))
        
        # Key Vault references
        if 'microsoft.keyvault/vaults' in rtype:
            kv_id = rid
            # Resources that reference this KV create a dependency
            for other_rid, other_r in resources_by_id.items():
                if other_rid == kv_id:
                    continue
                other_props = other_r.get('properties') or {}
                # Check for explicit KV reference in identity.userAssignedIdentities or managed identity
                identity = other_r.get('identity') or {}
                if identity.get('type') in ['UserAssigned', 'SystemAssigned, UserAssigned']:
                    signal = SignalSource(
                        type=SignalType.APP_CONFIG,
                        confidence=SIGNAL_CONFIDENCE[SignalType.APP_CONFIG],
                        evidence={
                            'reference_type': 'ManagedIdentity',
                            'key_vault': kv_id
                        },
                        timestamp=datetime.utcnow().isoformat() + 'Z',
                        source_resource='ManagedIdentity'
                    )
                    signals_list.append((other_rid, kv_id, [signal]))
    
    return signals_list


def _parse_connection_strings(
    connection_string: str,
    resources_by_id: Dict[str, Dict[str, Any]]
) -> List[Tuple[str, str]]:
    """
    Parse connection string and find matching resources.
    
    Returns: List of (resource_id, connection_type)
    """
    connections: List[Tuple[str, str]] = []
    
    # SQL Server pattern
    sql_match = re.search(
        r'Server=([^;,]+)',
        connection_string,
        re.IGNORECASE
    )
    if sql_match:
        server_name = sql_match.group(1).split('\\')[0].split('.')[0].lower()
        for rid, r in resources_by_id.items():
            if 'microsoft.sql/servers' in r.get('type', '').lower():
                if server_name in r.get('name', '').lower():
                    connections.append((rid, 'SQL'))
    
    # Cosmos DB pattern
    cosmos_match = re.search(
        r'AccountEndpoint=https://([^.]+)',
        connection_string,
        re.IGNORECASE
    )
    if cosmos_match:
        account_name = cosmos_match.group(1).lower()
        for rid, r in resources_by_id.items():
            if 'microsoft.documentdb/databaseaccounts' in r.get('type', '').lower():
                if account_name in r.get('name', '').lower():
                    connections.append((rid, 'CosmosDB'))
    
    # Storage Account pattern
    storage_match = re.search(
        r'DefaultEndpointsProtocol=https;AccountName=([^;]+)',
        connection_string,
        re.IGNORECASE
    )
    if storage_match:
        storage_name = storage_match.group(1).lower()
        for rid, r in resources_by_id.items():
            if 'microsoft.storage/storageaccounts' in r.get('type', '').lower():
                if storage_name in r.get('name', '').lower():
                    connections.append((rid, 'Storage'))
    
    # PostgreSQL pattern
    postgres_match = re.search(
        r'Server=([^;]+)',
        connection_string,
        re.IGNORECASE
    )
    if postgres_match and 'postgres' in connection_string.lower():
        server = postgres_match.group(1).split('.')[0].lower()
        for rid, r in resources_by_id.items():
            if 'microsoft.dbforpostgresql' in r.get('type', '').lower():
                if server in r.get('name', '').lower():
                    connections.append((rid, 'PostgreSQL'))
    
    # MySQL pattern
    mysql_match = re.search(
        r'Server=([^;]+)',
        connection_string,
        re.IGNORECASE
    )
    if mysql_match and 'mysql' in connection_string.lower():
        server = mysql_match.group(1).split('.')[0].lower()
        for rid, r in resources_by_id.items():
            if 'microsoft.dbformysql' in r.get('type', '').lower():
                if server in r.get('name', '').lower():
                    connections.append((rid, 'MySQL'))
    
    # Service Bus pattern
    sb_match = re.search(
        r'Endpoint=sb://([^.]+)',
        connection_string,
        re.IGNORECASE
    )
    if sb_match:
        namespace = sb_match.group(1).lower()
        for rid, r in resources_by_id.items():
            if 'microsoft.servicebus/namespaces' in r.get('type', '').lower():
                if namespace in r.get('name', '').lower():
                    connections.append((rid, 'ServiceBus'))
    
    return connections
