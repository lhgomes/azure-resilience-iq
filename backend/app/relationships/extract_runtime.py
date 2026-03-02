"""
Flow Logs and Application Insights data collection module.
Queries Log Analytics workspace for runtime signal data.
"""

from typing import Any, Dict, List
import json
from datetime import datetime, timedelta

from app.logger import get_logger
from app.storage._json_repo import write_json

try:
    from azure.monitor.query import LogsQueryClient
    HAS_MONITOR_QUERY = True
except ImportError:
    HAS_MONITOR_QUERY = False

LOGGER = get_logger(__name__)


def query_flow_logs(
    log_analytics_workspace_id: str,
    hours_back: int = 24,
    filter_statuses: List[str] = None
) -> List[Dict[str, Any]]:
    """
    Query Flow Logs from Log Analytics.
    
    Args:
        log_analytics_workspace_id: Workspace ID (resource ID)
        hours_back: Hours of data to query
        filter_statuses: Flow statuses to include (default: ['A'] for accepted)
    
    Returns:
        List of flow records with aggregated statistics
    """
    if not HAS_MONITOR_QUERY:
        LOGGER.warning("azure-monitor-query not installed. Install with: pip install azure-monitor-query")
        return []
    
    if filter_statuses is None:
        filter_statuses = ['A']  # Accepted flows only
    
    status_filter = ' or '.join(f"FlowStatus_s == '{s}'" for s in filter_statuses)
    
    query = f"""
    AzureNetworkAnalytics_CL
    | where ({status_filter})
    | where TimeGenerated > ago({hours_back}h)
    | project 
        SourceIP = SrcIP_s,
        DestIP = DstIP_s,
        SourcePort = SrcPort_d,
        DestPort = DstPort_d,
        Protocol = L4Protocol_s,
        BytesSent = SentBytes_d,
        BytesReceived = ReceivedBytes_d,
        FlowCount = AllowedInFlows_d
    | summarize 
        ConnectionCount = sum(FlowCount),
        TotalBytesSent = sum(BytesSent),
        TotalBytesReceived = sum(BytesReceived),
        ObservationCount = count()
        by SourceIP, DestIP, DestPort, Protocol
    | order by ConnectionCount desc
    """
    
    try:
        from azure.identity import DefaultAzureCredential
        credential = DefaultAzureCredential()
        client = LogsQueryClient(credential)
        
        LOGGER.info(f"Querying Flow Logs for {hours_back} hours...")
        result = client.query_workspace(log_analytics_workspace_id, query)
        
        flows = []
        if result.tables:
            for table in result.tables:
                for row in table.rows:
                    flows.append({
                        'source_ip': row[0],
                        'dest_ip': row[1],
                        'dest_port': int(row[2]) if row[2] else None,
                        'protocol': row[3],
                        'connection_count': row[4],
                        'total_bytes_sent': row[5],
                        'total_bytes_received': row[6],
                        'observation_count': row[7],
                        'bytes_transferred': (row[5] or 0) + (row[6] or 0)
                    })
        
        LOGGER.info(f"✔ Retrieved {len(flows)} flow records")
        return flows
    
    except Exception as e:
        LOGGER.error(f"Failed to query Flow Logs: {e}")
        return []


def query_application_insights(
    log_analytics_workspace_id: str,
    hours_back: int = 24,
    min_call_count: int = 10
) -> List[Dict[str, Any]]:
    """
    Query Application Insights dependencies from Log Analytics.
    
    Args:
        log_analytics_workspace_id: Workspace ID
        hours_back: Hours of data to query
        min_call_count: Minimum calls to include (filters noise)
    
    Returns:
        List of dependency records
    """
    if not HAS_MONITOR_QUERY:
        LOGGER.warning("azure-monitor-query not installed")
        return []
    
    query = f"""
    dependencies
    | where timestamp > ago({hours_back}h)
    | where success == true
    | project
        Source = cloud_RoleName,
        Target = target,
        TargetType = type,
        DurationMs = duration,
        Protocol = tostring(customDimensions.protocol),
        Status = resultCode,
        Timestamp = timestamp,
        OperationId = operation_Id
    | summarize
        CallCount = count(),
        AvgDuration = avg(DurationMs),
        MaxDuration = max(DurationMs),
        MinDuration = min(DurationMs),
        FailureCount = countif(Status startswith '5'),
        SuccessCount = countif(Status startswith '2'),
        ClientErrors = countif(Status startswith '4')
        by Source, Target, TargetType, Protocol
    | where CallCount > {min_call_count}
    | extend FailureRate = todouble(FailureCount) / CallCount
    | order by CallCount desc
    """
    
    try:
        from azure.identity import DefaultAzureCredential
        credential = DefaultAzureCredential()
        client = LogsQueryClient(credential)
        
        LOGGER.info(f"Querying Application Insights for {hours_back} hours...")
        result = client.query_workspace(log_analytics_workspace_id, query)
        
        dependencies = []
        if result.tables:
            for table in result.tables:
                for row in table.rows:
                    dependencies.append({
                        'source': row[0],
                        'target': row[1],
                        'target_type': row[2],
                        'avg_duration_ms': row[3],
                        'max_duration_ms': row[4],
                        'min_duration_ms': row[5],
                        'protocol': row[6],
                        'call_count': int(row[7]),
                        'failure_count': int(row[8]),
                        'success_count': int(row[9]),
                        'client_errors': int(row[10]),
                        'failure_rate': row[11]
                    })
        
        LOGGER.info(f"✔ Retrieved {len(dependencies)} dependency records")
        return dependencies
    
    except Exception as e:
        LOGGER.error(f"Failed to query Application Insights: {e}")
        return []


def query_network_watcher_diagnostics(
    subscription_id: str,
    resource_group: str,
    source_vm: str,
    dest_ip: str,
    dest_port: int = 443,
    protocol: str = 'TCP'
) -> Dict[str, Any]:
    """
    Run Network Watcher diagnostic to test connectivity.
    
    Args:
        subscription_id: Azure subscription ID
        resource_group: Resource group name
        source_vm: Source VM name
        dest_ip: Destination IP address
        dest_port: Destination port
        protocol: TCP or UDP
    
    Returns:
        Diagnostic results including reachability status
    """
    try:
        from azure.mgmt.network import NetworkManagementClient
        from azure.identity import DefaultAzureCredential
        
        credential = DefaultAzureCredential()
        client = NetworkManagementClient(credential, subscription_id)
        
        # This would require network watcher setup
        # Implementation depends on your Network Watcher configuration
        LOGGER.info(f"Running connectivity diagnostics: {source_vm} → {dest_ip}:{dest_port}")
        
        # Placeholder for actual diagnostic call
        return {
            'source': source_vm,
            'destination': f"{dest_ip}:{dest_port}",
            'protocol': protocol,
            'reachable': None,  # Set after actual diagnostic
            'diagnostics': {}
        }
    
    except Exception as e:
        LOGGER.error(f"Failed to run Network Watcher diagnostic: {e}")
        return {}


# Example: Save query results to file
def save_runtime_data(
    flow_logs: List[Dict[str, Any]],
    appinsights: List[Dict[str, Any]],
    output_dir: str = 'data/collector'
) -> None:
    """Save runtime data to JSON files for later processing"""
    from pathlib import Path
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Save Flow Logs
    flow_file = output_path / 'flow_logs.json'
    write_json(flow_file, flow_logs)
    LOGGER.info(f"✔ Saved {len(flow_logs)} flow records to {flow_file}")
    
    # Save App Insights
    appinsights_file = output_path / 'appinsights_dependencies.json'
    write_json(appinsights_file, appinsights)
    LOGGER.info(f"✔ Saved {len(appinsights)} dependency records to {appinsights_file}")


if __name__ == '__main__':
    # Example usage
    import logging
    
    logging.basicConfig(level=logging.INFO)
    
    # Configure these
    SUBSCRIPTION_ID = "<your-subscription-id>"
    LOG_ANALYTICS_WORKSPACE_ID = "<workspace-resource-id>"
    
    print("Note: This module requires:")
    print("  - pip install azure-monitor-query")
    print("  - az login (for authentication)")
    print("\nConfiguration:")
    print(f"  SUBSCRIPTION_ID: {SUBSCRIPTION_ID}")
    print(f"  LOG_ANALYTICS_WORKSPACE_ID: {LOG_ANALYTICS_WORKSPACE_ID}")
    
    # Uncomment to run queries:
    # flows = query_flow_logs(LOG_ANALYTICS_WORKSPACE_ID)
    # dependencies = query_application_insights(LOG_ANALYTICS_WORKSPACE_ID)
    # save_runtime_data(flows, dependencies)
