from typing import List, Dict, Any, Optional
from azure.mgmt.resourcegraph.models import QueryRequest
from .models import AzureResource
from .auth import get_arg_client


def build_filter_clause(
    resource_groups: Optional[List[str]],
    tags: Optional[Dict[str, str]]
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
        properties
    """

    request = QueryRequest(
        subscriptions=[subscription_id],
        query=query
    )

    response = client.resources(request)

    resources: List[AzureResource] = []

    for row in response.data:
        resources.append(
            AzureResource(
                id=row["id"],
                name=row["name"],
                type=row["type"],
                location=row.get("location"),
                resource_group=row["resourceGroup"],
                subscription_id=row["subscriptionId"],
                tags=row.get("tags") or {},
                properties=row.get("properties") or {}
            )
        )

    return resources
