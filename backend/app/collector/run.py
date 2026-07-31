import json
import argparse

from .arg import query_resources, query_subresources, query_role_assignments, normalize_id_fields, populate_backend_pool_ids, populate_session_affinity_info

from app.config import get_subscription_dir, get_resources_path, get_edges_path
from app.relationships.multi_source import MultiSourceAggregator
from app.relationships.extract_runtime import query_flow_logs, query_application_insights
from app.relationships.utils import norm_id, short_id
from app.graph.builder import edge_id
from app.resource_filters import load_monitored_resource_types
from app.storage._json_repo import read_json, write_json


def merge_collector_output(existing: object, collected: dict) -> dict:
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.update(collected)
    return merged

def normalize_resource_groups(resource_groups):
    return [rg.lower() for rg in resource_groups]

def discover_log_analytics_workspaces(resources_by_id):
    """
    Auto-discover Log Analytics workspace IDs from collected resources.
    
    Returns:
        tuple: (flow_logs_workspace_ids, appinsights_workspace_ids)
    """
    flow_logs_workspaces = set()
    appinsights_workspaces = set()
    
    for resource in resources_by_id.values():
        resource_type = (resource.get('type') or '').lower()
        properties = resource.get('properties', {})
        
        # Extract from Flow Logs
        if resource_type == 'microsoft.network/networkwatchers/flowlogs':
            flow_analytics = properties.get('flowAnalyticsConfiguration', {})
            nw_config = flow_analytics.get('networkWatcherFlowAnalyticsConfiguration', {})
            workspace_id = nw_config.get('workspaceResourceId')
            if workspace_id:
                flow_logs_workspaces.add(workspace_id)
        
        # Extract from Application Insights
        elif resource_type == 'microsoft.insights/components':
            workspace_id = properties.get('WorkspaceResourceId')
            if workspace_id:
                appinsights_workspaces.add(workspace_id)
    
    return list(flow_logs_workspaces), list(appinsights_workspaces)

def get_subscription_name(subscription_id: str) -> str:
    """Fetch subscription name from Azure."""
    try:
        from azure.mgmt.subscription import SubscriptionClient
        from azure.identity import DefaultAzureCredential
        
        credential = DefaultAzureCredential()
        client = SubscriptionClient(credential)
        subscription = client.subscriptions.get(subscription_id)
        return subscription.display_name or subscription_id
    except Exception:
        # Fallback to subscription ID if we can't fetch the name
        return subscription_id


def main():
    parser = argparse.ArgumentParser(description="Azure ARG Collector")
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument("--resource-group", action="append")
    parser.add_argument("--tag", action="append", help="key=value")
    parser.add_argument("--ignore-flowlog", action="store_true",
                       help="Skip Flow Logs collection (auto-discovered from resources by default)")
    parser.add_argument("--ignore-appinsights", action="store_true",
                       help="Skip Application Insights collection (auto-discovered from resources by default)")
    parser.add_argument("--log-analytics-workspace-id", action="append",
                       help="(Optional) Specify Log Analytics workspace ID(s) for runtime queries (repeatable). If not provided, workspaces will be auto-discovered from Flow Log and App Insights resources.")

    args = parser.parse_args()

    tags = None
    if args.tag:
        tags = dict(t.split("=", 1) for t in args.tag)

    allowed_types = load_monitored_resource_types()
    monitored_types = allowed_types or set()

    resources = query_resources(
        subscription_id=args.subscription_id,
        resource_groups=normalize_resource_groups(args.resource_group) if args.resource_group else None,
        tags=tags,
    )

    # Query subresources (Private DNS Zone Groups, Firewall Rules)
    print("🔍 Querying subresources (DNS zone groups, firewall rules)...")
    subresources = query_subresources(
        subscription_id=args.subscription_id,
        resource_groups=normalize_resource_groups(args.resource_group) if args.resource_group else None,
    )
    print(f"✔ Found {len(subresources)} subresources")

    # Query role assignments for permission-based relationships
    print("🔍 Querying role assignments (ACR pull, Key Vault, Storage)...")
    role_assignments = query_role_assignments(
        subscription_id=args.subscription_id,
        resource_groups=normalize_resource_groups(args.resource_group) if args.resource_group else None,
    )
    print(f"✔ Found {len(role_assignments)} relevant role assignments")

    # Combine all resources
    all_resources = resources + subresources

    # Convert to dicts early for post-processing
    all_resources_dicts = [r.model_dump() for r in all_resources]
    
    # Normalize all resource IDs first (before backend pool population)
    normalized_resources = []
    for r in all_resources_dicts:
        resource_dict = r
        
        # Normalize all id fields (main id and nested ids in properties)
        resource_dict = normalize_id_fields(resource_dict)
        
        # Ensure main fields are normalized
        resource_dict['id'] = norm_id(resource_dict['id'])
        resource_dict['short_id'] = short_id(resource_dict['id'])
        
        # Normalize parent and related id fields
        if resource_dict.get('parent_resource_id'):
            resource_dict['parent_resource_id'] = norm_id(resource_dict['parent_resource_id'])
        if resource_dict.get('backend_pool_ids'):
            resource_dict['backend_pool_ids'] = [norm_id(bid) for bid in resource_dict['backend_pool_ids']]
        if resource_dict.get('failover_group_id'):
            resource_dict['failover_group_id'] = norm_id(resource_dict['failover_group_id'])
        if resource_dict.get('child_resource_ids'):
            resource_dict['child_resource_ids'] = [norm_id(cid) for cid in resource_dict['child_resource_ids']]
        
        # Explicitly mark collected Azure resources as non-virtual
        resource_dict['virtual'] = False
        # Flag whether the resource type is in the monitored allowlist
        res_type = str(resource_dict.get('type', '')).lower()
        resource_dict['monitored'] = res_type in monitored_types
        
        normalized_resources.append(resource_dict)
    
    # Post-process: Populate backend_pool_ids from NIC resources (after normalization)
    print("🔗 Populating backend pool IDs from network interfaces...")
    populate_backend_pool_ids(normalized_resources)
    print("✔ Backend pool IDs populated")
    
    # Post-process: Extract session affinity information from LB and APGW
    print("🔗 Extracting session affinity configuration from load balancers and gateways...")
    populate_session_affinity_info(normalized_resources)
    print("✔ Session affinity information extracted")

    if allowed_types:
        monitored_count = sum(1 for r in normalized_resources if r.get('monitored'))
        print(f"ℹ️ Flagged {monitored_count}/{len(normalized_resources)} resources as monitored types")
    
    output = normalized_resources
    # Build lookup by normalized ID
    resources_by_id = {r['id']: r for r in output}

    # Get subscription name and add to output metadata
    subscription_name = get_subscription_name(args.subscription_id)
    
    # Create subscription-id based directory
    sub_dir = get_subscription_dir(args.subscription_id)
    sub_dir.mkdir(parents=True, exist_ok=True)
    
    out_file = get_resources_path(args.subscription_id)

    # Save collector-owned fields while preserving app-owned metadata.
    collected_output = {
        "subscription_id": args.subscription_id,
        "subscription_name": subscription_name,
        "resources": output,
        "role_assignments": role_assignments  # Include role assignments in output
    }
    resources_output = merge_collector_output(
        read_json(out_file, default={}),
        collected_output,
    )

    write_json(out_file, resources_output)

    print(f"✔ Collected {len(resources)} resources")
    print(f"✔ Subscription: {subscription_name}")
    print(f"✔ Written to {out_file}")
    
    # Extract multi-source signals
    print("📊 Extracting multi-source signals...")
    aggregator = MultiSourceAggregator(resources_by_id, role_assignments)
    
    # Prepare optional runtime data
    appinsights_data = None
    flow_logs_data = None
    
    # Auto-discover and query runtime data (unless explicitly ignored)
    if args.log_analytics_workspace_id:
        # Manual override - use specified workspace(s)
        print(f"⏳ Querying runtime data from specified workspace(s)...")
        flow_logs_workspaces = args.log_analytics_workspace_id
        appinsights_workspaces = args.log_analytics_workspace_id
    else:
        # Auto-discovery from collected resources
        print("🔍 Auto-discovering Log Analytics workspaces from collected resources...")
        flow_logs_workspaces, appinsights_workspaces = discover_log_analytics_workspaces(resources_by_id)
        
        if flow_logs_workspaces:
            print(f"  ✔ Found {len(flow_logs_workspaces)} Flow Logs workspace(s)")
        if appinsights_workspaces:
            print(f"  ✔ Found {len(appinsights_workspaces)} App Insights workspace(s)")
        
        if not flow_logs_workspaces and not appinsights_workspaces:
            print("  ⚠️  No Log Analytics workspaces found in collected resources")
            print("      Tip: Use --log-analytics-workspace-id to specify manually (repeatable)")
    
    # Query Flow Logs from discovered/specified workspaces (unless ignored)
    if not args.ignore_flowlog and flow_logs_workspaces:
        print("⏳ Querying Flow Logs...")
        flow_logs_data = []
        for workspace_id in flow_logs_workspaces:
            workspace_name = workspace_id.split('/')[-1] if '/' in workspace_id else workspace_id
            print(f"  → Workspace: {workspace_name}")
            data = query_flow_logs(workspace_id)
            if data:
                flow_logs_data.extend(data)
        if flow_logs_data:
            print(f"  ✔ Retrieved {len(flow_logs_data)} flow records total")
    elif args.ignore_flowlog:
        print("⊘ Flow Logs collection skipped (--ignore-flowlog)")
    
    # Query Application Insights from discovered/specified workspaces (unless ignored)
    if not args.ignore_appinsights and appinsights_workspaces:
        print("⏳ Querying Application Insights...")
        appinsights_data = []
        for workspace_id in appinsights_workspaces:
            workspace_name = workspace_id.split('/')[-1] if '/' in workspace_id else workspace_id
            print(f"  → Workspace: {workspace_name}")
            data = query_application_insights(workspace_id)
            if data:
                appinsights_data.extend(data)
        if appinsights_data:
            print(f"  ✔ Retrieved {len(appinsights_data)} dependency records total")
    elif args.ignore_appinsights:
        print("⊘ Application Insights collection skipped (--ignore-appinsights)")
    
    unified_edges = aggregator.extract_all_signals(
        appinsights_data=appinsights_data,
        flow_logs_data=flow_logs_data
    )
    
    # Format output
    edges_output = [
        {
            'id': edge_id(edge.source, edge.target, edge.relationship),
            'source': edge.source,
            'target': edge.target,
            'relationship': edge.relationship,
            'signals': [s['type'] for s in edge.signals],
            'signal_details': edge.signals,
            'confidence': round(edge.confidence, 3),
            'evidence': edge.evidence,
            'origin': edge.origin,
            'timestamp': edge.timestamp
        }
        for edge in unified_edges
    ]
    
    # Create edges directory
    edges_file = get_edges_path(args.subscription_id)
    edges_output_data = {
        "subscription_id": args.subscription_id,
        "subscription_name": subscription_name,
        "edges": edges_output
    }
    write_json(edges_file, edges_output_data)
    
    print(f"✔ Extracted {len(unified_edges)} unified edges with multi-source signals")
    print(f"✔ Written to {edges_file}")


if __name__ == "__main__":
    main()
