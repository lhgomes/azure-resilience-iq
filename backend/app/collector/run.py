import json
import argparse
from typing import List

from azure.identity import AzureCliCredential
from .arg import query_resources

from app.config import COLLECTOR_DIR
from app.relationships.multi_source import MultiSourceAggregator
from app.relationships.extract_runtime import query_flow_logs, query_application_insights
from app.relationships.utils import norm_id


OUTPUT_DIR = COLLECTOR_DIR
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_current_subscription() -> str:
    """
    Uses Azure CLI context.
    """
    credential = AzureCliCredential()
    token = credential.get_token("https://management.azure.com/.default")
    # Subscription is resolved by ARG using CLI context
    return None


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

    resources = query_resources(
        subscription_id=args.subscription_id,
        resource_groups=args.resource_group,
        tags=tags,
    )

    # Normalize all resource IDs at the source
    normalized_resources = []
    for r in resources:
        resource_dict = r.model_dump()
        resource_dict['id'] = norm_id(resource_dict['id'])
        normalized_resources.append(resource_dict)
    
    output = normalized_resources
    # Build lookup by normalized ID
    resources_by_id = {r['id']: r for r in output}

    out_file = OUTPUT_DIR / "resources.json"
    out_file.write_text(json.dumps(output, indent=2))

    print(f"✔ Collected {len(resources)} resources")
    print(f"✔ Written to {out_file}")
    
    # Extract multi-source signals
    print("📊 Extracting multi-source signals...")
    aggregator = MultiSourceAggregator(resources_by_id)
    
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
            'from': edge.from_id,
            'to': edge.to_id,
            'relationship': edge.relationship,
            'signals': [s['type'] for s in edge.signals],
            'signal_details': edge.signals,
            'confidence': round(edge.confidence, 3),
            'evidence': edge.evidence,
            'timestamp': edge.timestamp
        }
        for edge in unified_edges
    ]
    
    signals_file = OUTPUT_DIR / "unified_edges.json"
    signals_file.write_text(json.dumps(edges_output, indent=2))
    
    print(f"✔ Extracted {len(unified_edges)} unified edges with multi-source signals")
    print(f"✔ Written to {signals_file}")


if __name__ == "__main__":
    main()
