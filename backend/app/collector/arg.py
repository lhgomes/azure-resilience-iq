from typing import List, Dict, Any, Optional
from azure.mgmt.resourcegraph.models import QueryRequest
from .models import AzureResource
from .auth import get_arg_client
from app.relationships.utils import norm_id, is_azure_resource_id


def populate_backend_pool_ids(resources: List[Dict[str, Any]]) -> None:
    """
    Post-process resources to populate backend_pool_ids on VMs and VMSS.
    
    Extracts Load Balancer backend pool IDs from NIC resources and populates
    them on the VMs/VMSS that reference those NICs.
    
    This must be done after all resources are collected because we need to:
    1. Look up NIC resources to get their ipConfigurations
    2. Extract loadBalancerBackendAddressPools from ipConfigurations
    3. Map those backend pools back to the VMs that use those NICs
    """
    # Build lookup maps
    resources_by_id = {r['id']: r for r in resources}
    nic_to_backend_pools: Dict[str, List[str]] = {}
    
    # First pass: Extract backend pool IDs from all NICs
    for resource in resources:
        rtype = resource.get('type', '').lower()
        if 'microsoft.network/networkinterfaces' not in rtype:
            continue
        
        nic_id = resource['id']
        backend_pools = []
        
        # Extract from ipConfigurations
        ip_configs = resource.get('properties', {}).get('ipConfigurations', [])
        if not isinstance(ip_configs, list):
            continue
        
        for ip_config in ip_configs:
            ip_props = ip_config.get('properties', {}) if isinstance(ip_config, dict) else {}
            
            # Extract Load Balancer backend pools
            lb_pools = ip_props.get('loadBalancerBackendAddressPools', [])
            if isinstance(lb_pools, list):
                for pool in lb_pools:
                    if isinstance(pool, dict) and 'id' in pool:
                        pool_id = norm_id(pool['id'])
                        if pool_id not in backend_pools:
                            backend_pools.append(pool_id)
            
            # Extract Application Gateway backend pools
            ag_pools = ip_props.get('applicationGatewayBackendAddressPools', [])
            if isinstance(ag_pools, list):
                for pool in ag_pools:
                    if isinstance(pool, dict) and 'id' in pool:
                        pool_id = norm_id(pool['id'])
                        if pool_id not in backend_pools:
                            backend_pools.append(pool_id)
        
        if backend_pools:
            nic_to_backend_pools[nic_id] = backend_pools
    
    # Second pass: Populate backend_pool_ids on VMs and VMSS based on their NICs
    for resource in resources:
        rtype = resource.get('type', '').lower()
        
        # Handle VMs
        if 'microsoft.compute/virtualmachines' in rtype and '/extensions' not in rtype.lower():
            # Get NICs from networkProfile
            nic_refs = resource.get('properties', {}).get('networkProfile', {}).get('networkInterfaces', [])
            if not isinstance(nic_refs, list):
                continue
            
            all_backend_pools = []
            for nic_ref in nic_refs:
                if isinstance(nic_ref, dict) and 'id' in nic_ref:
                    nic_id = norm_id(nic_ref['id'])
                    if nic_id in nic_to_backend_pools:
                        all_backend_pools.extend(nic_to_backend_pools[nic_id])
            
            # Deduplicate and set
            if all_backend_pools:
                resource['backend_pool_ids'] = list(set(all_backend_pools))
        
        # Handle VMSS
        elif 'microsoft.compute/virtualmachinescalesets' in rtype:
            # VMSS references backend pools directly in virtualMachineProfile
            vm_profile = resource.get('properties', {}).get('virtualMachineProfile', {})
            network_profile = vm_profile.get('networkProfile', {})
            nic_configs = network_profile.get('networkInterfaceConfigurations', [])
            
            if not isinstance(nic_configs, list):
                continue
            
            all_backend_pools = []
            for nic_config in nic_configs:
                nic_props = nic_config.get('properties', {}) if isinstance(nic_config, dict) else {}
                ip_configs = nic_props.get('ipConfigurations', [])
                
                if not isinstance(ip_configs, list):
                    continue
                
                for ip_config in ip_configs:
                    ip_props = ip_config.get('properties', {}) if isinstance(ip_config, dict) else {}
                    
                    # Extract Load Balancer backend pools
                    lb_pools = ip_props.get('loadBalancerBackendAddressPools', [])
                    if isinstance(lb_pools, list):
                        for pool in lb_pools:
                            if isinstance(pool, dict) and 'id' in pool:
                                pool_id = norm_id(pool['id'])
                                if pool_id not in all_backend_pools:
                                    all_backend_pools.append(pool_id)
                    
                    # Extract Application Gateway backend pools
                    ag_pools = ip_props.get('applicationGatewayBackendAddressPools', [])
                    if isinstance(ag_pools, list):
                        for pool in ag_pools:
                            if isinstance(pool, dict) and 'id' in pool:
                                pool_id = norm_id(pool['id'])
                                if pool_id not in all_backend_pools:
                                    all_backend_pools.append(pool_id)
            
            if all_backend_pools:
                resource['backend_pool_ids'] = list(set(all_backend_pools))


def normalize_id_fields(data: Any) -> Any:
    """
    Recursively normalize all Azure resource IDs in a data structure to lowercase.
    Identifies Azure IDs by pattern (starts with /subscriptions/) rather than field name.
    This catches all Azure IDs regardless of field name: id, parentResourceId, failoverGroupId, etc.
    
    Handles strings, dicts, and lists.
    """
    if isinstance(data, dict):
        normalized = {}
        for key, value in data.items():
            if isinstance(value, str) and is_azure_resource_id(value):
                # Normalize any Azure resource ID, regardless of field name
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
        
        # Note: backend_pool_ids will be populated in post-processing
        # after all NICs are collected, since we need to look up NIC resources
        # to extract their loadBalancerBackendAddressPools references
        backend_pool_ids = None
        
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
