from typing import List, Dict, Any, Optional
from azure.mgmt.resourcegraph.models import QueryRequest
from .models import AzureResource
from .auth import get_arg_client


def build_filter_clause(
    resource_groups: Optional[List[str]],
    tags: Optional[Dict[str, str]],
    allowed_types: Optional[List[str]] = None,
) -> str:
    clauses = []

    if resource_groups:
        rg_list = ", ".join(f"'{rg}'" for rg in resource_groups)
        clauses.append(f"resourceGroup in ({rg_list})")

    if allowed_types:
        types_list = ", ".join(f"'{t}'" for t in allowed_types)
        clauses.append(f"type in~ ({types_list})")

    if tags:
        for k, v in tags.items():
            clauses.append(f"tags['{k}'] == '{v}'")

    if not clauses:
        return ""

    return " | where " + " and ".join(clauses)


def query_resources(
    subscription_id: str,
    resource_groups: Optional[List[str]] = None,
    tags: Optional[Dict[str, str]] = None,
    allowed_types: Optional[List[str]] = None,
) -> List[AzureResource]:

    client = get_arg_client()

    filter_clause = build_filter_clause(resource_groups, tags, allowed_types)

    query = f"""
    Resources
    {filter_clause}
    | project
        id,
        name,
        type,
        location,
        resourceGroup,
        subscriptionId,
        tags,
        properties,
        sku,
        zones,
        availabilitySet = properties.availabilitySet.id,
        vmScaleSetId = properties.virtualMachineScaleSet.id,
        failoverGroupId = properties.failoverGroupId,
        replicationRegions = properties.replicationRegions,
        networkInterfaces = properties.networkProfile.networkInterfaces
    """

    request = QueryRequest(
        subscriptions=[subscription_id],
        query=query
    )

    response = client.resources(request)

    resources: List[AzureResource] = []

    for row in response.data:
        # Extract parent resource relationships
        parent_resource_id = row.get("availabilitySet") or row.get("vmScaleSetId") or row.get("failoverGroupId")
        parent_resource_type = None
        if row.get("availabilitySet"):
            parent_resource_type = "Microsoft.Compute/availabilitySets"
        elif row.get("vmScaleSetId"):
            parent_resource_type = "Microsoft.Compute/virtualMachineScaleSets"
        elif row.get("failoverGroupId"):
            parent_resource_type = "Microsoft.Sql/servers/failoverGroups"
        
        # Extract backend pool IDs from network interfaces
        backend_pool_ids = None
        network_interfaces = row.get("networkInterfaces")
        if network_interfaces and isinstance(network_interfaces, list):
            backend_pool_ids = []
            for nic in network_interfaces:
                if isinstance(nic, dict) and "id" in nic:
                    backend_pool_ids.append(nic["id"])
        
        resources.append(
            AzureResource(
                id=row["id"],
                name=row["name"],
                type=row["type"],
                location=row.get("location"),
                resource_group=row["resourceGroup"],
                subscription_id=row["subscriptionId"],
                tags=row.get("tags") or {},
                properties=row.get("properties") or {},
                sku=row.get("sku"),
                zones=row.get("zones"),
                parent_resource_id=parent_resource_id,
                parent_resource_type=parent_resource_type,
                backend_pool_ids=backend_pool_ids,
                failover_group_id=row.get("failoverGroupId"),
                replica_regions=row.get("replicationRegions") if isinstance(row.get("replicationRegions"), dict) else None
            )
        )

    return resources
