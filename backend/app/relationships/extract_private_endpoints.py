from __future__ import annotations
from typing import Any, Dict, List, Tuple, Set
from .utils import norm_id, safe_get, parent_id

def extract_private_endpoint_relationships(resources_by_id: Dict[str, Dict[str, Any]]) -> Tuple[
    List[Tuple[str, str, str, str, float, list]],
    Set[str]
]:
    """
    Returns (edges, synthetic_node_ids)
    Edge tuples: (source, target, relationship, source_type, confidence, evidence_list)
    """
    edges: List[Tuple[str, str, str, str, float, list]] = []
    synthetic: Set[str] = set()

    # Private Endpoints
    for rid, r in resources_by_id.items():
        rtype = (r.get("type") or "").lower()
        if rtype != "microsoft.network/privateendpoints":
            continue

        props: Dict[str, Any] = r.get("properties") or {}

        # PE -> Subnet
        subnet_id = safe_get(props, "subnet.id")
        if isinstance(subnet_id, str) and subnet_id.strip():
            sid = norm_id(subnet_id)
            synthetic.add(sid)
            edges.append((
                rid,
                sid,
                "connected_to_subnet",
                "arg",
                0.95,
                [{"field": "privateEndpoint.properties.subnet.id", "value": subnet_id}],
            ))

            vnet_id = parent_id(sid, "/subnets/")
            if vnet_id:
                edges.append((
                    sid,
                    vnet_id,
                    "belongs_to_vnet",
                    "heuristic",
                    0.85,
                    [{"rule": "parent_id(subnet, /subnets/)"}],
                ))

        # PE -> Target resource(s)
        conns = props.get("privateLinkServiceConnections") or []
        conns += props.get("manualPrivateLinkServiceConnections") or []

        for c in conns:
            target_id = safe_get(c or {}, "properties.privateLinkServiceId")
            if isinstance(target_id, str) and target_id.strip():
                edges.append((
                    rid,
                    norm_id(target_id),
                    "private_link_to",
                    "arg",
                    0.98,
                    [{"field": "privateLinkServiceConnections[].properties.privateLinkServiceId", "value": target_id}],
                ))

    # DNS Zone Groups (subresource)
    # Type: Microsoft.Network/privateEndpoints/privateDnsZoneGroups
    for rid, r in resources_by_id.items():
        rtype = (r.get("type") or "").lower()
        if rtype != "microsoft.network/privateendpoints/privatednszonegroups":
            continue

        # parent is the PE resource id (strip after /privateDnsZoneGroups/)
        # rid looks like: .../privateEndpoints/<peName>/privateDnsZoneGroups/<groupName>
        pe_id = parent_id(rid, "/privatednszonegroups/")
        if not pe_id:
            continue

        props: Dict[str, Any] = r.get("properties") or {}
        configs = props.get("privateDnsZoneConfigs") or []

        for cfg in configs:
            zone_id = safe_get(cfg or {}, "properties.privateDnsZoneId")
            if isinstance(zone_id, str) and zone_id.strip():
                edges.append((
                    pe_id,
                    norm_id(zone_id),
                    "uses_private_dns_zone",
                    "arg",
                    0.9,
                    [{"field": "privateDnsZoneConfigs[].properties.privateDnsZoneId", "value": zone_id}],
                ))

    # Resources with privateEndpointConnections (like KeyVault, Storage, Registry)
    # Extract DNS zone associations from privateEndpointConnections[].properties.privateDnsZoneConfigs[]
    for rid, r in resources_by_id.items():
        rtype = (r.get("type") or "").lower()
        
        # KeyVault, Storage, Container Registry, etc. can have private endpoints
        if rtype not in [
            "microsoft.keyvault/vaults",
            "microsoft.storage/storageaccounts",
            "microsoft.containerregistry/registries",
            "microsoft.dbforpostgresql/flexibleservers",
            "microsoft.dbformysql/flexibleservers",
            "microsoft.sql/servers"
        ]:
            continue

        props: Dict[str, Any] = r.get("properties") or {}
        pe_conns = props.get("privateEndpointConnections") or []

        for pe_conn in pe_conns:
            conn_props = (pe_conn or {}).get("properties") or {}
            
            # Extract private DNS zone configs from the connection
            dns_zone_configs = conn_props.get("privateDnsZoneConfigs") or []
            
            for dns_cfg in dns_zone_configs:
                zone_id = safe_get(dns_cfg or {}, "properties.privateDnsZoneId")
                zone_name = safe_get(dns_cfg or {}, "name")
                
                if isinstance(zone_id, str) and zone_id.strip():
                    # Resource → Private DNS Zone via private endpoint
                    edges.append((
                        rid,
                        norm_id(zone_id),
                        "uses_private_dns_zone",
                        "arg",
                        0.85,
                        [{"field": "privateEndpointConnections[].privateDnsZoneConfigs[].privateDnsZoneId", "value": zone_id}],
                    ))
                elif isinstance(zone_name, str) and zone_name.strip():
                    # Fallback: try to find DNS zone by name if ID not available
                    # This is useful when the zone ID is not populated but the name is
                    for resource_id, resource in resources_by_id.items():
                        if "microsoft.network/privatednszones" in (resource.get("type") or "").lower():
                            if (resource.get("name") or "").lower() == zone_name.lower():
                                edges.append((
                                    rid,
                                    resource_id,
                                    "uses_private_dns_zone",
                                    "heuristic",
                                    0.8,
                                    [{"field": "privateEndpointConnections[].privateDnsZoneConfigs[].name", "value": zone_name}],
                                ))
                                break

    return edges, synthetic
