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

            edges.append((
                rid,
                sid_n,
                "contains_subnet",
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

            # subnet -> vnet (derived)
            vnet_id = parent_id(sid_n, "/subnets/")
            if vnet_id:
                edges.append((
                    sid_n,
                    vnet_id,
                    "belongs_to_vnet",
                    "heuristic",
                    0.85,
                    [{"rule": "parent_id(subnet, /subnets/)"}],
                ))

    # NIC -> Subnet / NSG
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

    return edges, synthetic
