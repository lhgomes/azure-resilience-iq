from typing import List, Dict, Any, Optional
from azure.mgmt.resourcegraph.models import QueryRequest
from .models import AzureResource
from .auth import get_arg_client
from app.relationships.utils import norm_id


def normalize_id_fields(data: Any) -> Any:
    """
    Recursively normalize all 'id' fields in a data structure to lowercase.
    Handles strings, dicts, and lists.
    """
    if isinstance(data, dict):
        normalized = {}
        for key, value in data.items():
            if key == "id" and isinstance(value, str):
                # Normalize any field named 'id'
                normalized[key] = norm_id(value)
            else:
                # Recursively normalize nested structures
                normalized[key] = normalize_id_fields(value)
        return normalized
    elif isinstance(data, list):
        return [normalize_id_fields(item) for item in data]
    else:
        return data


def build_filter_clause(
    resource_groups: Optional[List[str]],
    tags: Optional[Dict[str, str]],
) -> str:
    clauses = []

    if resource_groups:
        rg_list = ", ".join(f"'{rg}'" for rg in resource_groups)
        clauses.append(f"resourceGroup in ({rg_list})")

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
) -> List[AzureResource]:

    client = get_arg_client()

    filter_clause = build_filter_clause(resource_groups, tags)

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
        
        # Normalize parent resource ID
        if parent_resource_id:
            parent_resource_id = norm_id(parent_resource_id)
        
        # Extract backend pool IDs from network interfaces
        backend_pool_ids = None
        network_interfaces = row.get("networkInterfaces")
        if network_interfaces and isinstance(network_interfaces, list):
            backend_pool_ids = []
            for nic in network_interfaces:
                if isinstance(nic, dict) and "id" in nic:
                    # Normalize network interface ID
                    backend_pool_ids.append(norm_id(nic["id"]))
        
        # Normalize all id fields in properties recursively
        normalized_properties = normalize_id_fields(row.get("properties") or {})
        
        resources.append(
            AzureResource(
                id=row["id"],
                name=row["name"],
                type=row["type"],
                location=row.get("location"),
                resource_group=row["resourceGroup"],
                subscription_id=row["subscriptionId"],
                tags=row.get("tags") or {},
                properties=normalized_properties,
                sku=row.get("sku"),
                zones=row.get("zones"),
                parent_resource_id=parent_resource_id,
                parent_resource_type=parent_resource_type,
                backend_pool_ids=backend_pool_ids,
                failover_group_id=norm_id(row.get("failoverGroupId")) if row.get("failoverGroupId") else None,
                replica_regions=row.get("replicationRegions") if isinstance(row.get("replicationRegions"), dict) else None
            )
        )

    return resources


def query_subresources(
    subscription_id: str,
    resource_groups: Optional[List[str]] = None,
) -> List[AzureResource]:
    """
    Query critical subresources that contain relationship data:
    - Private DNS Zone Groups (for private endpoint → DNS zone mappings)
    - Database firewall rules (for database → compute network rules)
    - Database VNet rules (for database → subnet mappings)
    """
    client = get_arg_client()

    # Build resource group filter
    rg_filter = ""
    if resource_groups:
        rg_list = ", ".join(f"'{rg}'" for rg in resource_groups)
        rg_filter = f"| where resourceGroup in ({rg_list})"

    query = f"""
    Resources
    | where type in~ (
        "microsoft.network/privateendpoints/privatednszonegroups",
        "microsoft.dbformysql/flexibleservers/firewallrules",
        "microsoft.dbformysql/flexibleservers/virtualnetworkrules",
        "microsoft.dbforpostgresql/flexibleservers/firewallrules",
        "microsoft.dbforpostgresql/flexibleservers/virtualnetworkrules",
        "microsoft.sql/servers/firewallrules",
        "microsoft.sql/servers/virtualnetworkrules"
    )
    {rg_filter}
    | project
        id,
        name,
        type,
        location,
        resourceGroup,
        subscriptionId,
        tags,
        properties
    """

    request = QueryRequest(
        subscriptions=[subscription_id],
        query=query
    )

    response = client.resources(request)

    resources: List[AzureResource] = []

    for row in response.data:
        # Normalize all id fields in properties recursively
        normalized_properties = normalize_id_fields(row.get("properties") or {})

        resources.append(
            AzureResource(
                id=row["id"],
                name=row["name"],
                type=row["type"],
                location=row.get("location"),
                resource_group=row["resourceGroup"],
                subscription_id=row["subscriptionId"],
                tags=row.get("tags") or {},
                properties=normalized_properties,
            )
        )

    return resources


def query_role_assignments(
    subscription_id: str,
    resource_groups: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Query role assignments to identify permission-based relationships:
    - AcrPull role assignments (compute → ACR)
    - Key Vault access (compute → Key Vault)
    - Storage access (compute → Storage)
    
    Returns lightweight dicts rather than AzureResource objects since
    these are authorization resources, not infrastructure resources.
    """
    client = get_arg_client()

    # Build resource group filter
    rg_filter = ""
    if resource_groups:
        rg_list = ", ".join(f"'{rg}'" for rg in resource_groups)
        rg_filter = f"| where properties.scope contains 'resourceGroups' and (resourceGroup in ({rg_list}) or properties.scope has_any ({rg_list}))"

    # Role definition IDs for key roles:
    # - 7f951dda-4ed3-4680-a7ca-43fe172d538d = AcrPull
    # - 00482a5a-887f-4fb3-b303-3b6cf2a1e45f = Key Vault Secrets User
    # - ba92f5b4-2d11-453d-a403-e96b0029c9fe = Storage Blob Data Contributor
    # - 2a2b9908-6ea1-4ae2-8e65-a410df84e7d1 = Storage Blob Data Reader

    query = f"""
    authorizationresources
    | where type == "microsoft.authorization/roleassignments"
    | where properties.roleDefinitionId has_any (
        "7f951dda-4ed3-4680-a7ca-43fe172d538d",
        "00482a5a-887f-4fb3-b303-3b6cf2a1e45f",
        "ba92f5b4-2d11-453d-a403-e96b0029c9fe",
        "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1"
    )
    {rg_filter}
    | project
        id,
        name,
        type,
        subscriptionId,
        principalId = properties.principalId,
        roleDefinitionId = properties.roleDefinitionId,
        scope = properties.scope,
        principalType = properties.principalType
    """

    request = QueryRequest(
        subscriptions=[subscription_id],
        query=query
    )

    response = client.resources(request)

    assignments = []
    for row in response.data:
        # Normalize the scope (it's a resource ID)
        scope = norm_id(row.get("scope", ""))
        
        assignments.append({
            "id": row["id"],
            "name": row["name"],
            "principal_id": row.get("principalId"),
            "role_definition_id": row.get("roleDefinitionId", ""),
            "scope": scope,
            "principal_type": row.get("principalType"),
            "subscription_id": row["subscriptionId"],
        })

    return assignments
