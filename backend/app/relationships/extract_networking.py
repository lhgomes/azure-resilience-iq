from __future__ import annotations
from typing import Any, Dict, List, Tuple, Set
from .utils import norm_id, safe_get, parent_id

def extract_networking_relationships(resources_by_id: Dict[str, Dict[str, Any]]) -> Tuple[
    List[Tuple[str, str, str, str, float, list]],
    Set[str]
]:
    """
    Returns (edges, synthetic_node_ids)
    synthetic_node_ids = subnet ids that are referenced but may not be present as resources
    Edge tuples: (source, target, relationship, source_type, confidence, evidence_list)
    """
    edges: List[Tuple[str, str, str, str, float, list]] = []
    synthetic: Set[str] = set()

    # VNet -> Subnet (contains)
    for rid, r in resources_by_id.items():
        rtype = (r.get("type") or "").lower()
        if rtype != "microsoft.network/virtualnetworks":
            continue

        props: Dict[str, Any] = r.get("properties") or {}
        subnets = props.get("subnets") or []

        for s in subnets:
            sid = (s or {}).get("id")
            if not isinstance(sid, str) or not sid.strip():
                continue

            sid_n = norm_id(sid)
            synthetic.add(sid_n)

            # Prefer subnet -> VNet direction so the dependent resource points to its parent
            edges.append((
                sid_n,
                rid,
                "belongs_to_vnet",
                "arg",
                0.98,
                [{"field": "virtualNetworks.properties.subnets[].id", "value": sid}],
            ))

            # subnet -> NSG
            nsg_id = safe_get(s or {}, "properties.networkSecurityGroup.id")
            if isinstance(nsg_id, str) and nsg_id.strip():
                edges.append((
                    sid_n,
                    norm_id(nsg_id),
                    "protected_by_nsg",
                    "arg",
                    0.9,
                    [{"field": "subnet.properties.networkSecurityGroup.id", "value": nsg_id}],
                ))

            # subnet -> route table
            rt_id = safe_get(s or {}, "properties.routeTable.id")
            if isinstance(rt_id, str) and rt_id.strip():
                edges.append((
                    sid_n,
                    norm_id(rt_id),
                    "routed_by_route_table",
                    "arg",
                    0.9,
                    [{"field": "subnet.properties.routeTable.id", "value": rt_id}],
                ))

    # NIC -> Subnet / NSG / Public IP
    for rid, r in resources_by_id.items():
        rtype = (r.get("type") or "").lower()
        if rtype != "microsoft.network/networkinterfaces":
            continue

        props: Dict[str, Any] = r.get("properties") or {}
        ipcfgs = props.get("ipConfigurations") or []

        for ipcfg in ipcfgs:
            subnet_id = safe_get(ipcfg or {}, "properties.subnet.id")
            if isinstance(subnet_id, str) and subnet_id.strip():
                sid_n = norm_id(subnet_id)
                synthetic.add(sid_n)
                edges.append((
                    rid,
                    sid_n,
                    "attached_to_subnet",
                    "arg",
                    0.9,
                    [{"field": "nic.ipConfigurations[].properties.subnet.id", "value": subnet_id}],
                ))

            # NIC -> Public IP
            pubip_id = safe_get(ipcfg or {}, "properties.publicIPAddress.id")
            if isinstance(pubip_id, str) and pubip_id.strip():
                pubip_id_n = norm_id(pubip_id)
                if pubip_id_n in resources_by_id:
                    edges.append((
                        rid,
                        pubip_id_n,
                        "uses_public_ip",
                        "arg",
                        0.95,
                        [{"field": "nic.ipConfigurations[].properties.publicIPAddress.id", "value": pubip_id}],
                    ))

        nsg_id = safe_get(props, "networkSecurityGroup.id")
        if isinstance(nsg_id, str) and nsg_id.strip():
            edges.append((
                rid,
                norm_id(nsg_id),
                "protected_by_nsg",
                "arg",
                0.85,
                [{"field": "nic.properties.networkSecurityGroup.id", "value": nsg_id}],
            ))

    # Azure Firewall -> Public IP (ipConfigurations)
    for rid, r in resources_by_id.items():
        rtype = (r.get("type") or "").lower()
        if rtype != "microsoft.network/azurefirewalls":
            continue

        props: Dict[str, Any] = r.get("properties") or {}
        ipcfgs = props.get("ipConfigurations") or []

        for ipcfg in ipcfgs:
            pubip_id = safe_get(ipcfg or {}, "properties.publicIPAddress.id")
            if isinstance(pubip_id, str) and pubip_id.strip():
                pubip_id_n = norm_id(pubip_id)
                if pubip_id_n in resources_by_id:
                    edges.append((
                        rid,
                        pubip_id_n,
                        "uses_public_ip",
                        "arg",
                        0.95,
                        [{"field": "azurefirewall.ipConfigurations[].properties.publicIPAddress.id", "value": pubip_id}],
                    ))

    # Bastion Host -> Public IP (ipConfigurations)
    for rid, r in resources_by_id.items():
        rtype = (r.get("type") or "").lower()
        if rtype != "microsoft.network/bastionhosts":
            continue

        props: Dict[str, Any] = r.get("properties") or {}
        ipcfgs = props.get("ipConfigurations") or []

        for ipcfg in ipcfgs:
            pubip_id = safe_get(ipcfg or {}, "properties.publicIPAddress.id")
            if isinstance(pubip_id, str) and pubip_id.strip():
                pubip_id_n = norm_id(pubip_id)
                if pubip_id_n in resources_by_id:
                    edges.append((
                        rid,
                        pubip_id_n,
                        "uses_public_ip",
                        "arg",
                        0.95,
                        [{"field": "bastionhost.ipConfigurations[].properties.publicIPAddress.id", "value": pubip_id}],
                    ))

    # Application Gateway -> Public IP (frontendIPConfigurations)
    for rid, r in resources_by_id.items():
        rtype = (r.get("type") or "").lower()
        if rtype != "microsoft.network/applicationgateways":
            continue

        props: Dict[str, Any] = r.get("properties") or {}
        frontend_ipcfgs = props.get("frontendIPConfigurations") or []

        for ipcfg in frontend_ipcfgs:
            pubip_id = safe_get(ipcfg or {}, "properties.publicIPAddress.id")
            if isinstance(pubip_id, str) and pubip_id.strip():
                pubip_id_n = norm_id(pubip_id)
                if pubip_id_n in resources_by_id:
                    edges.append((
                        rid,
                        pubip_id_n,
                        "uses_public_ip",
                        "arg",
                        0.95,
                        [{"field": "applicationgateway.frontendIPConfigurations[].properties.publicIPAddress.id", "value": pubip_id}],
                    ))

    # Load Balancer -> Public IP (frontendIPConfigurations)
    for rid, r in resources_by_id.items():
        rtype = (r.get("type") or "").lower()
        if rtype != "microsoft.network/loadbalancers":
            continue

        props: Dict[str, Any] = r.get("properties") or {}
        frontend_ipcfgs = props.get("frontendIPConfigurations") or []

        for ipcfg in frontend_ipcfgs:
            pubip_id = safe_get(ipcfg or {}, "properties.publicIPAddress.id")
            if isinstance(pubip_id, str) and pubip_id.strip():
                pubip_id_n = norm_id(pubip_id)
                if pubip_id_n in resources_by_id:
                    edges.append((
                        rid,
                        pubip_id_n,
                        "uses_public_ip",
                        "arg",
                        0.95,
                        [{"field": "loadbalancer.frontendIPConfigurations[].properties.publicIPAddress.id", "value": pubip_id}],
                    ))
        
        # Load Balancer -> VMSS (via backend address pools)
        # Backend pools contain references to NICs, which we can try to resolve to VMSS
        backend_pools = props.get("backendAddressPools") or []
        
        for backend_pool in backend_pools:
            bp_props = (backend_pool or {}).get("properties") or {}
            backend_ip_configs = bp_props.get("backendIPConfigurations") or []
            
            # Collect VMSS that have NICs in this backend pool
            vmss_ids = set()
            
            for ip_cfg in backend_ip_configs:
                nic_id = ip_cfg.get("id") if isinstance(ip_cfg, dict) else None
                
                if isinstance(nic_id, str) and nic_id.strip():
                    nic_id_n = norm_id(nic_id)
                    
                    # Try to find the NIC and get its VMSS parent
                    if nic_id_n in resources_by_id:
                        nic = resources_by_id[nic_id_n]
                        # NIC has virtualMachineScaleSet.id property
                        vmss_id = safe_get(nic.get("properties") or {}, "virtualMachineScaleSet.id")
                        if isinstance(vmss_id, str) and vmss_id.strip():
                            vmss_ids.add(norm_id(vmss_id))
                    else:
                        # Heuristic: Extract VMSS from NIC ID if it matches pattern
                        # Pattern: .../virtualMachineScaleSets/{vmssName}/virtualMachines/{vmId}/networkInterfaces/{nicName}
                        if "virtualmachinescalesets" in nic_id_n:
                            # Extract VMSS ID from NIC path
                            parts = nic_id_n.split("/")
                            try:
                                vmss_idx = next(i for i, p in enumerate(parts) if p.lower() == "virtualmachinescalesets")
                                if vmss_idx + 1 < len(parts):
                                    # Reconstruct VMSS ID
                                    vmss_id_candidate = "/".join(parts[:vmss_idx + 2])
                                    vmss_ids.add(norm_id(vmss_id_candidate))
                            except (StopIteration, IndexError):
                                pass
            
            # Create edges for each VMSS found
            for vmss_id in vmss_ids:
                if vmss_id in resources_by_id:
                    edges.append((
                        rid,
                        vmss_id,
                        "depends_on",
                        "heuristic",
                        0.75,
                        [{"field": "loadbalancer.backendAddressPools[].backendIPConfigurations[].id", "rule": "VMSS inference from NIC"}],
                    ))

    # AKS Cluster -> Public IP (effectiveOutboundIPs)
    for rid, r in resources_by_id.items():
        rtype = (r.get("type") or "").lower()
        if rtype != "microsoft.containerservice/managedclusters":
            continue

        props: Dict[str, Any] = r.get("properties") or {}
        outbound_ips = safe_get(props, "networkProfile.loadBalancerProfile.effectiveOutboundIPs") or []

        if isinstance(outbound_ips, list):
            for outbound_ip in outbound_ips:
                pubip_id = (outbound_ip or {}).get("id")
                if isinstance(pubip_id, str) and pubip_id.strip():
                    pubip_id_n = norm_id(pubip_id)
                    if pubip_id_n in resources_by_id:
                        edges.append((
                            rid,
                            pubip_id_n,
                            "uses_public_ip",
                            "arg",
                            0.95,
                            [{"field": "managedcluster.networkProfile.loadBalancerProfile.effectiveOutboundIPs[].id", "value": pubip_id}],
                        ))

    return edges, synthetic
