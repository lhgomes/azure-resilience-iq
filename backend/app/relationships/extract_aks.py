from __future__ import annotations
from typing import Any, Dict, List, Tuple
from .utils import norm_id, safe_get

def extract_aks_relationships(resources_by_id: Dict[str, Dict[str, Any]]) -> List[Tuple[str, str, str, str, float, list]]:
    """
    Returns edges as tuples:
    (from_id, to_id, relationship, source, confidence, evidence)
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

    return edges
