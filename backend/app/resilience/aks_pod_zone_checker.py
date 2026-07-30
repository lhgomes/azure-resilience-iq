"""
AKS Pod Zone Distribution Checker

Validates that workload pods are actually distributed across availability zones
by querying Container Insights (Log Analytics) data.

This complements the ARM-level node pool zone configuration check by verifying
the runtime scheduling reality, not just the configuration intent.

Requires Container Insights (omsagent addon) to be enabled on the cluster.
Degrades gracefully to "unknown" when not enabled, and skips entirely when
Container Insights data is unavailable (e.g., query failure, no recent data).
"""

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from app.collector.auth import get_arg_client
from azure.mgmt.resourcegraph.models import QueryRequest

LOGGER = logging.getLogger(__name__)

RECOMMENDATION_ID = "aks-pod-zone-distribution-001"

# System/platform namespaces excluded from zone distribution checks
_SYSTEM_NAMESPACES = frozenset({
    "kube-system",
    "gatekeeper-system",
    "azure-arc",
    "kube-public",
    "kube-node-lease",
    "monitoring",
    "cert-manager",
    "ingress-nginx",
})

# Uses Container Insights tables: KubeNodeInventory, KubePodInventory
_POD_ZONE_KQL = """\
let NodeZones =
    KubeNodeInventory
    | where TimeGenerated > ago(2h)
    | where ClusterId =~ "{cluster_id}"
    | extend Zone = tostring(parse_json(tostring(Labels))["topology.kubernetes.io/zone"])
    | where isnotempty(Zone)
    | summarize Zone = anyif(Zone, isnotempty(Zone)) by Computer;
KubePodInventory
| where TimeGenerated > ago(2h)
| where ClusterId =~ "{cluster_id}"
| where PodStatus == "Running"
| where Namespace !in ({excluded_namespaces})
| summarize PodCount = count() by Computer, Namespace
| join kind=inner NodeZones on Computer
| summarize
    DistinctZones = dcount(Zone),
    TotalPods = sum(PodCount),
    ZoneList = make_set(Zone)"""


def _extract_container_insights_config(cluster_resource: Dict[str, Any]) -> Optional[str]:
    """
    Extract the Log Analytics workspace resource ID from the cluster's omsagent addon.

    Returns the workspace ARM resource ID, or None if Container Insights is not enabled.
    """
    props = cluster_resource.get("properties", {})
    addon_profiles = props.get("addonProfiles", {})
    omsagent = addon_profiles.get("omsagent", {})

    if not omsagent.get("enabled"):
        return None

    config = omsagent.get("config", {})
    return config.get("logAnalyticsWorkspaceResourceID") or None


def _get_workspace_customer_id(workspace_resource_id: str) -> Optional[str]:
    """
    Resolve the Log Analytics workspace GUID (customerId) from its ARM resource ID.

    The Monitor query API requires the workspace GUID, not the ARM resource ID.
    We look it up via Resource Graph so we can reuse the existing credential path.
    """
    parts = workspace_resource_id.lower().split("/")
    try:
        sub_idx = parts.index("subscriptions")
        workspace_sub_id = parts[sub_idx + 1]
    except (ValueError, IndexError):
        LOGGER.debug("Cannot parse subscription from workspace resource ID: %s", workspace_resource_id)
        return None

    kql = (
        "Resources\n"
        f'| where type =~ "microsoft.operationalinsights/workspaces"\n'
        f'| where id =~ "{workspace_resource_id}"\n'
        "| project customerId = tostring(properties.customerId)\n"
        "| limit 1"
    )

    client = get_arg_client()
    request = QueryRequest(subscriptions=[workspace_sub_id], query=kql)
    try:
        response = client.resources(request)
        rows = list(response.data or [])
        if rows and rows[0].get("customerId"):
            return str(rows[0]["customerId"])
    except Exception as exc:
        LOGGER.debug("Failed to resolve workspace customer ID for %s: %s", workspace_resource_id, exc)

    return None


def _query_pod_zones(
    workspace_customer_id: str, cluster_resource_id: str
) -> Optional[Dict[str, Any]]:
    """
    Query Container Insights for pod zone distribution.

    Returns a dict with DistinctZones, TotalPods, ZoneList, or None on failure.
    """
    try:
        from azure.monitor.query import LogsQueryClient, LogsQueryStatus
        from azure.identity import DefaultAzureCredential
    except ImportError:
        LOGGER.warning(
            "azure-monitor-query is not installed; install it to enable AKS pod zone checks"
        )
        return None

    credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
    client = LogsQueryClient(credential)

    excluded_ns = ", ".join(f'"{ns}"' for ns in sorted(_SYSTEM_NAMESPACES))
    kql = _POD_ZONE_KQL.format(
        cluster_id=cluster_resource_id,
        excluded_namespaces=excluded_ns,
    )

    try:
        response = client.query_workspace(
            workspace_id=workspace_customer_id,
            query=kql,
            timespan=timedelta(hours=2),
        )
    except Exception as exc:
        LOGGER.debug(
            "Pod zone KQL query failed for cluster %s: %s", cluster_resource_id, exc
        )
        return None

    if response.status != LogsQueryStatus.SUCCESS:
        LOGGER.debug(
            "Pod zone query non-success for %s: %s", cluster_resource_id, response.status
        )
        return None

    if not response.tables or not response.tables[0].rows:
        return None

    table = response.tables[0]
    columns = [col.name for col in table.columns]
    row = dict(zip(columns, table.rows[0]))

    return {
        "distinct_zones": int(row.get("DistinctZones") or 0),
        "total_pods": int(row.get("TotalPods") or 0),
        "zone_list": sorted(list(row.get("ZoneList") or [])),
    }


def _get_node_pool_zone_count(cluster_resource: Dict[str, Any]) -> int:
    """Return the number of availability zones configured across node pools."""
    props = cluster_resource.get("properties", {})

    agent_pools = props.get("agentPoolProfiles", [])
    if agent_pools and isinstance(agent_pools, list):
        max_zones = max(
            (len(pool.get("availabilityZones") or []) for pool in agent_pools),
            default=0,
        )
        if max_zones > 0:
            return max_zones

    # Terraform-style property name
    default_pool = props.get("default_node_pool", {})
    if isinstance(default_pool, dict):
        zones = default_pool.get("availability_zones") or []
        if zones:
            return len(zones)

    return 0


def _check_terraform_workload_spread(
    cluster_resource: Dict[str, Any],
    base_check: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Evaluate pod zone distribution via static Terraform workload analysis.

    Reads the kubernetes_workloads_zone_spread summary stored on the cluster
    resource by the Terraform generator.  Returns None when no Kubernetes
    workloads were found in the Terraform (cannot infer anything useful).
    """
    spread = (cluster_resource.get("properties") or {}).get("kubernetes_workloads_zone_spread")

    if not spread:
        # No kubernetes_deployment / kubernetes_stateful_set resources in the Terraform
        return {
            **base_check,
            "status": "unknown",
            "validation_source": "TerraformStaticAnalysis",
            "long_description": (
                "No kubernetes_deployment or kubernetes_stateful_set resources were found "
                "in the Terraform configuration. Cannot verify pod zone distribution. "
                "If workloads are deployed separately (Helm, kubectl), add "
                "topologySpreadConstraints with topology.kubernetes.io/zone to your "
                "Deployment and StatefulSet manifests."
            ),
        }

    total: int = spread.get("workload_count", 0)
    without: int = spread.get("without_zone_spread", 0)
    missing_names: List[str] = spread.get("workloads_without_spread", [])

    if without == 0:
        return {
            **base_check,
            "status": "pass",
            "validation_source": "TerraformStaticAnalysis",
            "long_description": (
                f"All {total} Kubernetes workload(s) in the Terraform configuration have "
                "topologySpreadConstraints with topology.kubernetes.io/zone configured."
            ),
        }

    missing_list = ", ".join(missing_names[:5])
    if len(missing_names) > 5:
        missing_list += f" (+{len(missing_names) - 5} more)"

    return {
        **base_check,
        "status": "fail",
        "validation_source": "TerraformStaticAnalysis",
        "long_description": (
            f"{without} of {total} Kubernetes workload(s) are missing "
            "topologySpreadConstraints with topology.kubernetes.io/zone: "
            f"{missing_list}. "
            "Add a topologySpreadConstraints block to each Deployment and StatefulSet "
            "spec to ensure pods are scheduled across availability zones."
        ),
    }


def check_aks_pod_zone_distribution(
    cluster_resource: Dict[str, Any],
    subscription_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Check that workload pods are distributed across availability zones.

    Returns a resilience check dict compatible with the APRL evaluation format,
    or None when the check cannot provide meaningful data (e.g., no pods running,
    Container Insights data unavailable).
    """
    from app.resilience.aprl_integration import generate_resilience_check_id

    cluster_id = cluster_resource.get("id", "")
    cluster_name = cluster_resource.get("name", "unknown")

    base_check: Dict[str, Any] = {
        "recommendation_id": RECOMMENDATION_ID,
        "description": "AKS workload pods distributed across availability zones",
        "category": "HighAvailability",
        "impact": "High",
        "long_description": (
            "Verifies that running workload pods are scheduled across multiple availability "
            "zones, not just that node pools are zone-aware. Pod placement depends on "
            "topology spread constraints and node affinity rules, which are independent of "
            "node pool zone configuration."
        ),
        "potential_benefits": (
            "Prevents a single zone failure from taking down all pod replicas. "
            "Validates actual runtime scheduling against node pool zone configuration."
        ),
        "learn_more": [
            {
                "name": "Topology spread constraints",
                "url": "https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraints/",
            },
            {
                "name": "AKS availability zones best practices",
                "url": "https://learn.microsoft.com/azure/aks/availability-zones",
            },
        ],
        "validation_source": "ContainerInsights",
        "resilience_check_id": generate_resilience_check_id(cluster_id, RECOMMENDATION_ID),
    }

    workspace_resource_id = _extract_container_insights_config(cluster_resource)

    # For virtual (Terraform) resources, use static workload analysis instead
    if cluster_resource.get("virtual"):
        return _check_terraform_workload_spread(cluster_resource, base_check)

    if not workspace_resource_id:
        return {
            **base_check,
            "status": "unknown",
            "long_description": (
                "Cannot verify pod zone distribution: Container Insights is not enabled on "
                f"cluster '{cluster_name}'. Enable the monitoring add-on to gain runtime "
                "visibility into pod scheduling across availability zones."
            ),
        }

    workspace_guid = _get_workspace_customer_id(workspace_resource_id)
    if not workspace_guid:
        LOGGER.debug(
            "Could not resolve workspace GUID for cluster %s, skipping pod zone check",
            cluster_name,
        )
        return None

    zone_data = _query_pod_zones(workspace_guid, cluster_id)
    if zone_data is None:
        LOGGER.debug("No Container Insights pod data available for cluster %s", cluster_name)
        return None

    distinct_zones: int = zone_data["distinct_zones"]
    total_pods: int = zone_data["total_pods"]
    zone_list: List[str] = zone_data["zone_list"]
    node_pool_zones: int = _get_node_pool_zone_count(cluster_resource)

    if total_pods == 0:
        # No user workload pods running - nothing meaningful to report
        return None

    zones_str = ", ".join(zone_list) if zone_list else "unknown"

    if distinct_zones >= 3:
        return {
            **base_check,
            "status": "pass",
            "long_description": (
                f"{total_pods} running workload pods are distributed across {distinct_zones} "
                f"availability zones ({zones_str}). The cluster is resilient to single-zone failures."
            ),
        }

    if distinct_zones == 2:
        # Pass when node pools only span 2 zones; fail when 3 are available but pods don't use them
        status = "fail" if node_pool_zones >= 3 else "pass"
        advice = (
            "Add topologySpreadConstraints to your deployments to spread pods across all 3 zones."
            if node_pool_zones >= 3
            else "Pods are distributed across all configured availability zones."
        )
        return {
            **base_check,
            "status": status,
            "long_description": (
                f"{total_pods} running workload pods are across {distinct_zones} zones "
                f"({zones_str}). {advice}"
            ),
        }

    # distinct_zones <= 1
    return {
        **base_check,
        "status": "fail",
        "long_description": (
            f"All {total_pods} running workload pods are concentrated in a single zone "
            f"({zones_str}). Node pools are configured with {node_pool_zones} zone(s), "
            "but pod scheduling is not distributing workloads across zones. "
            "Add topologySpreadConstraints to your Deployments and StatefulSets to ensure "
            "zone-resilient workload placement."
        ),
    }
