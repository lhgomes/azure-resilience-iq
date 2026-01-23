"""
Extract relationships from database firewall rules, VNet rules, and role assignments.
"""
from __future__ import annotations
from typing import Any, Dict, List, Tuple, Set
from .utils import norm_id, safe_get, parent_id


def extract_database_firewall_relationships(resources_by_id: Dict[str, Dict[str, Any]]) -> List[
    Tuple[str, str, str, str, float, list]
]:
    """
    Extract relationships from database firewall rules and VNet rules.
    
    Returns edges list:
    Edge tuples: (source, target, relationship, source_type, confidence, evidence_list)
    """
    edges: List[Tuple[str, str, str, str, float, list]] = []

    # MySQL/PostgreSQL Firewall Rules
    for rid, r in resources_by_id.items():
        rtype = (r.get("type") or "").lower()
        
        # MySQL Flexible Server Firewall Rules
        if "microsoft.dbformysql/flexibleservers/firewallrules" in rtype:
            # Parent is the MySQL server
            # rid: .../Microsoft.DBforMySQL/flexibleServers/<serverName>/firewallRules/<ruleName>
            server_id = parent_id(rid, "/firewallrules/")
            if not server_id:
                continue
            
            props = r.get("properties") or {}
            start_ip = props.get("startIpAddress", "")
            end_ip = props.get("endIpAddress", "")
            
            # Note: We can't directly map IPs to specific compute resources without additional data
            # This creates awareness that a firewall rule exists
            edges.append((
                server_id,
                rid,
                "has_firewall_rule",
                "arg",
                0.95,
                [{"field": "firewallRule", "startIp": start_ip, "endIp": end_ip}],
            ))
        
        # PostgreSQL Flexible Server Firewall Rules
        elif "microsoft.dbforpostgresql/flexibleservers/firewallrules" in rtype:
            server_id = parent_id(rid, "/firewallrules/")
            if not server_id:
                continue
            
            props = r.get("properties") or {}
            start_ip = props.get("startIpAddress", "")
            end_ip = props.get("endIpAddress", "")
            
            edges.append((
                server_id,
                rid,
                "has_firewall_rule",
                "arg",
                0.95,
                [{"field": "firewallRule", "startIp": start_ip, "endIp": end_ip}],
            ))
        
        # SQL Server Firewall Rules
        elif rtype == "microsoft.sql/servers/firewallrules":
            server_id = parent_id(rid, "/firewallrules/")
            if not server_id:
                continue
            
            props = r.get("properties") or {}
            start_ip = props.get("startIpAddress", "")
            end_ip = props.get("endIpAddress", "")
            
            edges.append((
                server_id,
                rid,
                "has_firewall_rule",
                "arg",
                0.95,
                [{"field": "firewallRule", "startIp": start_ip, "endIp": end_ip}],
            ))

    # MySQL/PostgreSQL VNet Rules
    for rid, r in resources_by_id.items():
        rtype = (r.get("type") or "").lower()
        
        # MySQL VNet Rules
        if "microsoft.dbformysql/flexibleservers/virtualnetworkrules" in rtype:
            server_id = parent_id(rid, "/virtualnetworkrules/")
            if not server_id:
                continue
            
            props = r.get("properties") or {}
            subnet_id = props.get("virtualNetworkSubnetId", "")
            
            if subnet_id:
                # Database server -> Subnet (VNet rule allows this subnet)
                edges.append((
                    server_id,
                    norm_id(subnet_id),
                    "allows_subnet",
                    "arg",
                    0.9,
                    [{"field": "virtualNetworkSubnetId", "value": subnet_id}],
                ))
                
                # Now we can infer: resources in this subnet can access the database
                # This helps with VMSS -> DB relationships if VMSS is in the same subnet
        
        # PostgreSQL VNet Rules
        elif "microsoft.dbforpostgresql/flexibleservers/virtualnetworkrules" in rtype:
            server_id = parent_id(rid, "/virtualnetworkrules/")
            if not server_id:
                continue
            
            props = r.get("properties") or {}
            subnet_id = props.get("virtualNetworkSubnetId", "")
            
            if subnet_id:
                edges.append((
                    server_id,
                    norm_id(subnet_id),
                    "allows_subnet",
                    "arg",
                    0.9,
                    [{"field": "virtualNetworkSubnetId", "value": subnet_id}],
                ))
        
        # SQL Server VNet Rules
        elif rtype == "microsoft.sql/servers/virtualnetworkrules":
            server_id = parent_id(rid, "/virtualnetworkrules/")
            if not server_id:
                continue
            
            props = r.get("properties") or {}
            subnet_id = props.get("virtualNetworkSubnetId", "")
            
            if subnet_id:
                edges.append((
                    server_id,
                    norm_id(subnet_id),
                    "allows_subnet",
                    "arg",
                    0.9,
                    [{"field": "virtualNetworkSubnetId", "value": subnet_id}],
                ))

    # Infer compute -> database relationships based on subnet matching
    # Find all compute resources and their subnets
    compute_subnet_map = {}  # {subnet_id: [compute_resource_ids]}
    
    for rid, r in resources_by_id.items():
        rtype = (r.get("type") or "").lower()
        props = r.get("properties") or {}
        
        # VMSS
        if rtype == "microsoft.compute/virtualmachinescalesets":
            vm_profile = props.get("virtualMachineProfile") or {}
            net_profile = vm_profile.get("networkProfile") or {}
            nic_configs = net_profile.get("networkInterfaceConfigurations") or []
            
            for nic_config in nic_configs:
                ip_configs = safe_get(nic_config, "properties.ipConfigurations") or []
                for ip_config in ip_configs:
                    subnet_id = safe_get(ip_config, "properties.subnet.id")
                    if subnet_id:
                        subnet_id = norm_id(subnet_id)
                        if subnet_id not in compute_subnet_map:
                            compute_subnet_map[subnet_id] = []
                        compute_subnet_map[subnet_id].append(rid)
        
        # VM
        elif rtype == "microsoft.compute/virtualmachines":
            net_profile = props.get("networkProfile") or {}
            nics = net_profile.get("networkInterfaces") or []
            
            for nic_ref in nics:
                nic_id = safe_get(nic_ref, "id")
                if nic_id and nic_id in resources_by_id:
                    nic = resources_by_id[nic_id]
                    nic_props = nic.get("properties") or {}
                    ip_configs = nic_props.get("ipConfigurations") or []
                    
                    for ip_config in ip_configs:
                        subnet_id = safe_get(ip_config, "properties.subnet.id")
                        if subnet_id:
                            subnet_id = norm_id(subnet_id)
                            if subnet_id not in compute_subnet_map:
                                compute_subnet_map[subnet_id] = []
                            compute_subnet_map[subnet_id].append(rid)
    
    # Now find database -> subnet edges and create compute -> database edges
    db_subnet_edges = [e for e in edges if e[2] == "allows_subnet"]
    
    for db_id, subnet_id, rel, source, conf, evidence in db_subnet_edges:
        # Find compute resources in this subnet
        compute_resources = compute_subnet_map.get(subnet_id, [])
        
        for compute_id in compute_resources:
            edges.append((
                compute_id,
                db_id,
                "can_access_database",
                "heuristic",
                0.7,  # Lower confidence - inferred from subnet matching
                [{"rule": "compute_in_subnet_with_db_vnet_rule", "subnet": subnet_id}],
            ))
    
    return edges


def extract_role_assignment_relationships(
    resources_by_id: Dict[str, Dict[str, Any]],
    role_assignments: List[Dict[str, Any]]
) -> List[Tuple[str, str, str, str, float, list]]:
    """
    Extract relationships from role assignments (ACR pull, Key Vault access, Storage access).
    
    Args:
        resources_by_id: Resource lookup
        role_assignments: List of role assignment dicts from query_role_assignments()
    
    Returns edges list:
        Edge tuples: (source, target, relationship, source_type, confidence, evidence_list)
    """
    edges: List[Tuple[str, str, str, str, float, list]] = []
    
    # Build principal -> identity map (which resource has which managed identity)
    principal_to_resource = {}  # {principal_id: resource_id}
    
    for rid, r in resources_by_id.items():
        identity = r.get("identity") or {}
        principal_id = identity.get("principalId")
        
        if principal_id:
            principal_to_resource[principal_id] = rid
    
    # Role definition ID to relationship type mapping
    role_relationships = {
        "7f951dda-4ed3-4680-a7ca-43fe172d538d": "pulls_from_acr",  # AcrPull
        "00482a5a-887f-4fb3-b303-3b6cf2a1e45f": "reads_secrets_from",  # Key Vault Secrets User
        "ba92f5b4-2d11-453d-a403-e96b0029c9fe": "writes_to_storage",  # Storage Blob Data Contributor
        "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1": "reads_from_storage",  # Storage Blob Data Reader
    }
    
    for assignment in role_assignments:
        principal_id = assignment.get("principal_id")
        scope = assignment.get("scope")  # The target resource
        role_def_id = assignment.get("role_definition_id", "")
        
        # Extract role GUID from full role definition ID
        role_guid = None
        if "/" in role_def_id:
            role_guid = role_def_id.split("/")[-1].lower()
        
        # Find which resource has this principal (the source)
        source_resource_id = principal_to_resource.get(principal_id)
        
        if not source_resource_id or not scope:
            continue
        
        # Determine relationship type from role
        relationship = role_relationships.get(role_guid, "has_rbac_access_to")
        
        edges.append((
            source_resource_id,
            scope,
            relationship,
            "rbac",
            0.85,  # High confidence - explicit RBAC assignment
            [{"role": role_guid, "assignment_id": assignment.get("id")}],
        ))
    
    return edges
