import json
import argparse

from .arg import query_resources, query_subresources, query_role_assignments, normalize_id_fields, populate_backend_pool_ids

from app.config import get_subscription_dir, get_resources_path, get_edges_path
from app.relationships.multi_source import MultiSourceAggregator
from app.relationships.extract_runtime import query_flow_logs, query_application_insights
from app.relationships.utils import norm_id, short_id
from app.graph.builder import edge_id
from app.resource_filters import load_monitored_resource_types

def normalize_resource_groups(resource_groups):
    return [rg.lower() for rg in resource_groups]

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
    parser.add_argument("--include-runtime", action="store_true", 
                       help="Include runtime data (Flow Logs, App Insights)")
    parser.add_argument("--log-analytics-workspace-id", 
                       help="Log Analytics workspace ID for runtime queries")

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

    # Post-process: Populate backend_pool_ids from NIC resources
    print("🔗 Populating backend pool IDs from network interfaces...")
    all_resources_dicts = [r.model_dump() for r in all_resources]
    populate_backend_pool_ids(all_resources_dicts)
    print("✔ Backend pool IDs populated")

    # Normalize all resource IDs at the source
    normalized_resources = []
    for r in all_resources_dicts:
        resource_dict = r  # Already converted to dict above
        
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
    
    # Save resources with subscription metadata
    resources_output = {
        "subscription_id": args.subscription_id,
        "subscription_name": subscription_name,
        "resources": output,
        "role_assignments": role_assignments  # Include role assignments in output
    }
    
    out_file = get_resources_path(args.subscription_id)
    out_file.write_text(json.dumps(resources_output, indent=2))

    print(f"✔ Collected {len(resources)} resources")
    print(f"✔ Subscription: {subscription_name}")
    print(f"✔ Written to {out_file}")
    
    # Extract multi-source signals
    print("📊 Extracting multi-source signals...")
    aggregator = MultiSourceAggregator(resources_by_id, role_assignments)
    
    # Prepare optional runtime data (stub for now)
    appinsights_data = None
    flow_logs_data = None
    
    if args.include_runtime and args.log_analytics_workspace_id:
        print("⏳ Querying Application Insights and Flow Logs...")
        flow_logs_data = query_flow_logs(args.log_analytics_workspace_id)
        appinsights_data = query_application_insights(args.log_analytics_workspace_id)
    
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
    edges_file.write_text(json.dumps(edges_output_data, indent=2))
    
    print(f"✔ Extracted {len(unified_edges)} unified edges with multi-source signals")
    print(f"✔ Written to {edges_file}")


if __name__ == "__main__":
    main()
