"""Read Azure Service Groups (and their members) via Azure Resource Graph.

Service Groups live at tenant scope, so these queries omit ``subscriptions`` and
run against the tenant. Membership is modelled as ``servicegroupmember``
relationship resources whose ``TargetId`` is the Service Group ARM id.
"""

from __future__ import annotations

import re
from typing import Dict, List

from azure.mgmt.resourcegraph.models import QueryRequest, QueryRequestOptions

from app.collector.auth import get_arg_client
from app.relationships.utils import norm_id

# ARM id form for a Service Group: /providers/Microsoft.Management/serviceGroups/<name>
_SERVICE_GROUP_ID_PREFIX = "/providers/Microsoft.Management/serviceGroups/"

# Same constraint ARM enforces on the name segment. Validating before we
# interpolate the name into KQL closes the door on query injection.
_SERVICE_GROUP_NAME_RE = re.compile(r"^[a-zA-Z0-9\-_().]{1,90}$")


def _run_tenant_query(query: str) -> List[Dict]:
    """Execute a tenant-scoped ARG query, following skip-token pagination."""
    client = get_arg_client()
    rows: List[Dict] = []
    skip_token: str | None = None
    while True:
        options = QueryRequestOptions(skip_token=skip_token) if skip_token else None
        response = client.resources(QueryRequest(query=query, options=options))
        rows.extend(response.data or [])
        skip_token = getattr(response, "skip_token", None)
        if not skip_token:
            break
    return rows


def list_service_groups() -> List[Dict[str, str]]:
    """Return every Service Group visible to the caller's tenant."""
    query = (
        "resourcecontainers "
        '| where type == "microsoft.management/servicegroups" '
        "| project id, name, displayName = tostring(properties.displayName), parentResourceId = tostring(properties.parent.resourceId)"
    )
    results: List[Dict[str, str]] = []
    for row in _run_tenant_query(query):
        name = row.get("name") or ""
        if not name:
            continue
        results.append(
            {
                "id": row.get("id") or f"{_SERVICE_GROUP_ID_PREFIX}{name}",
                "name": name,
                "display_name": row.get("displayName") or name,
                "parent_service_group_id": row.get("parentResourceId") or None,
            }
        )
    return results


def list_service_group_member_ids(service_group_name: str) -> List[str]:
    """Return the normalized resource IDs that belong to a Service Group."""
    seen: set[str] = set()
    members: List[str] = []
    for rel in list_service_group_member_relationships(service_group_name):
        normalized = rel["source_id"]
        if normalized and normalized not in seen:
            seen.add(normalized)
            members.append(normalized)
    return members


def list_service_group_member_relationships(service_group_name: str) -> List[Dict[str, str]]:
    """Return the ``serviceGroupMember`` relationship resources targeting a SG.

    Each entry carries the relationship's full ARM ``relationship_id`` and
    ``name`` (whatever named it — this tool, IaC, or the portal) alongside the
    normalized member ``source_id``. Callers use the real ids to detach members
    or to detect existing membership, instead of assuming a relationship name.
    """
    if not _SERVICE_GROUP_NAME_RE.match(service_group_name or ""):
        raise ValueError(f"Invalid service group name: {service_group_name!r}")

    target_id = f"{_SERVICE_GROUP_ID_PREFIX}{service_group_name}"
    query = (
        "relationshipresources "
        '| where type == "microsoft.relationships/servicegroupmember" '
        f"| where tolower(tostring(properties.TargetId)) == tolower('{target_id}') "
        "| project relationshipId = id, name, sourceId = tostring(properties.SourceId)"
    )

    results: List[Dict[str, str]] = []
    for row in _run_tenant_query(query):
        source_id = row.get("sourceId")
        relationship_id = row.get("relationshipId")
        if not source_id or not relationship_id:
            continue
        results.append(
            {
                "relationship_id": relationship_id,
                "name": row.get("name") or "",
                "source_id": norm_id(source_id) or source_id,
            }
        )
    return results
