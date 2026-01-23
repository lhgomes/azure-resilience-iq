from __future__ import annotations
from typing import Any, Dict, List, Tuple
from .utils import norm_id, safe_get

def extract_aks_relationships(resources_by_id: Dict[str, Dict[str, Any]]) -> List[Tuple[str, str, str, str, float, list]]:
    """
    Returns edges as tuples:
    (source, target, relationship, source_type, confidence, evidence)
    """
    edges: List[Tuple[str, str, str, str, float, list]] = []

    for rid, r in resources_by_id.items():
        rtype = (r.get("type") or "").lower()
        if rtype != "microsoft.containerservice/managedclusters":
            continue

        props: Dict[str, Any] = r.get("properties") or {}
        aps = props.get("agentPoolProfiles") or []
        subnet_ids: List[str] = []

        # agentPoolProfiles[].vnetSubnetId
        for ap in aps:
            sid = (ap or {}).get("vnetSubnetId")
            if isinstance(sid, str) and sid.strip():
                subnet_ids.append(sid)

        # sometimes networkProfile has subnet-like IDs
        np = props.get("networkProfile") or {}
        for key in ["podSubnetId", "subnetId", "vnetSubnetId"]:
            sid = np.get(key)
            if isinstance(sid, str) and sid.strip():
                subnet_ids.append(sid)

        # de-dup
        seen = set()
        subnet_ids = [s for s in subnet_ids if not (norm_id(s) in seen or seen.add(norm_id(s)))]

        for sid in subnet_ids:
            edges.append((
                rid,
                norm_id(sid),
                "connects_to_subnet",
                "arg",
                0.95,
                [{"field": "agentPoolProfiles.vnetSubnetId/networkProfile.*SubnetId", "value": sid}],
            ))

        # AKS → Load Balancers in managed node RG
        # Pattern: Extract from managed RG based on cluster name/location
        location = r.get("location", "").lower()
        resource_group = r.get("resource_group", "").lower()
        cluster_name = r.get("name", "").lower()
        
        if location and cluster_name:
            # Managed RG follows pattern: mc_{resource_group}_{cluster_name}_{location}
            managed_rg_pattern = f"mc_{resource_group}_{cluster_name}_{location}".lower()
            
            # Find all LBs in the managed node RG
            for resource_id, resource in resources_by_id.items():
                if "microsoft.network/loadbalancers" in (resource.get("type") or "").lower():
                    if managed_rg_pattern in resource_id.lower():
                        edges.append((
                            rid,
                            resource_id,
                            "depends_on",
                            "heuristic",
                            0.75,
                            [{"rule": "AKS managed node RG inference", "pattern": managed_rg_pattern}],
                        ))
        
        # AKS → Private DNS Zone (for private clusters)
        api_server_profile = props.get("apiServerAccessProfile") or {}
        if api_server_profile.get("privateCluster"):
            # Construct the DNS zone name: privatelink.{region}.azmk8s.io
            dns_zone_name = f"privatelink.{location}.azmk8s.io"
            
            # Find the matching private DNS zone
            for resource_id, resource in resources_by_id.items():
                if "microsoft.network/privatednszones" in (resource.get("type") or "").lower():
                    resource_name = (resource.get("name") or "").lower()
                    if resource_name == dns_zone_name:
                        edges.append((
                            rid,
                            resource_id,
                            "uses_private_dns_zone",
                            "heuristic",
                            0.75,
                            [{"rule": "AKS private cluster DNS zone inference", "dns_zone": dns_zone_name}],
                        ))
        
        # AKS → Effective Outbound IPs are already captured in networking module
        # via loadBalancerProfile.effectiveOutboundIPs

    return edges
