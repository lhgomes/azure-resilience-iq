from __future__ import annotations

import json
from typing import List, Optional

from .models import (
    ArtifactFormat,
    ServiceGroupArtifact,
    ServiceGroupMemberRef,
    SERVICE_GROUP_API_VERSION,
    SERVICE_GROUP_MEMBER_API_VERSION,
)


def _hcl_escape(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace('"', '\\"')


def _comment_safe(value: str) -> str:
    return (value or "").replace("\n", " ").replace("\r", " ").strip()


def _member_comment(member: ServiceGroupMemberRef) -> str:
    label = _comment_safe(member.display_name or member.resource_id)
    azure_type = _comment_safe(member.azure_type or "")
    return f"{label} ({azure_type})" if azure_type else label


def render_terraform(
    service_group_name: str,
    display_name: str,
    members: List[ServiceGroupMemberRef],
    parent_service_group_id: Optional[str] = None,
) -> str:
    lines: List[str] = [
        "terraform {",
        "  required_providers {",
        "    azapi = {",
        '      source = "Azure/azapi"',
        "    }",
        "  }",
        "}",
        "",
        'provider "azapi" {}',
        "",
    ]

    if parent_service_group_id:
        parent_expr = f'"{_hcl_escape(parent_service_group_id)}"'
    else:
        lines += [
            "# Current tenant — the root Service Group (id == tenant id) is the parent.",
            'data "azapi_client_config" "current" {}',
            "",
        ]
        parent_expr = (
            '"/providers/Microsoft.Management/serviceGroups/'
            '${data.azapi_client_config.current.tenant_id}"'
        )

    lines += [
        f"# Service Group: {_comment_safe(display_name)}",
        'resource "azapi_resource" "service_group" {',
        f'  type      = "Microsoft.Management/serviceGroups@{SERVICE_GROUP_API_VERSION}"',
        f'  name      = "{_hcl_escape(service_group_name)}"',
        '  parent_id = "/"',
        "  body = {",
        "    properties = {",
        f'      displayName = "{_hcl_escape(display_name)}"',
        "      parent = {",
        f"        resourceId = {parent_expr}",
        "      }",
        "    }",
        "  }",
        "}",
    ]

    for index, member in enumerate(members):
        lines.extend(
            [
                "",
                f"# Member: {_member_comment(member)}",
                f'resource "azapi_resource" "member_{index}" {{',
                f'  type      = "Microsoft.Relationships/serviceGroupMember@{SERVICE_GROUP_MEMBER_API_VERSION}"',
                f'  name      = "{_hcl_escape(member.member_name)}"',
                f'  parent_id = "{_hcl_escape(member.resource_id)}"',
                "  body = {",
                "    properties = {",
                "      targetId = azapi_resource.service_group.id",
                "    }",
                "  }",
                "}",
            ]
        )

    return "\n".join(lines) + "\n"


def render_arm(
    service_group_name: str,
    display_name: str,
    members: List[ServiceGroupMemberRef],
    parent_service_group_id: Optional[str] = None,
) -> str:
    service_group_ref = (
        f"[tenantResourceId('Microsoft.Management/serviceGroups', "
        f"'{service_group_name}')]"
    )
    parent_resource_id = (
        parent_service_group_id
        if parent_service_group_id
        else "[tenantResourceId('Microsoft.Management/serviceGroups', tenant().tenantId)]"
    )

    resources: List[dict] = [
        {
            "type": "Microsoft.Management/serviceGroups",
            "apiVersion": SERVICE_GROUP_API_VERSION,
            "name": service_group_name,
            "properties": {
                "displayName": display_name,
                "parent": {
                    "resourceId": parent_resource_id
                },
            },
        }
    ]

    for member in members:
        resources.append(
            {
                "type": "Microsoft.Relationships/serviceGroupMember",
                "apiVersion": SERVICE_GROUP_MEMBER_API_VERSION,
                "name": member.member_name,
                "scope": member.resource_id,
                "dependsOn": [service_group_ref],
                "properties": {"targetId": service_group_ref},
            }
        )

    template = {
        "$schema": "https://schema.management.azure.com/schemas/2019-08-01/tenantDeploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "metadata": {
            "description": f"Service Group '{display_name}' and its members.",
        },
        "resources": resources,
    }

    return json.dumps(template, indent=2) + "\n"


def generate_artifact(
    fmt: ArtifactFormat,
    service_group_name: str,
    display_name: str,
    members: List[ServiceGroupMemberRef],
    parent_service_group_id: Optional[str] = None,
) -> ServiceGroupArtifact:
    if fmt == ArtifactFormat.terraform:
        content = render_terraform(service_group_name, display_name, members, parent_service_group_id)
        filename = f"{service_group_name}.tf"
    elif fmt == ArtifactFormat.arm:
        content = render_arm(service_group_name, display_name, members, parent_service_group_id)
        filename = f"{service_group_name}.json"
    else:  # pragma: no cover - guarded by pydantic enum
        raise ValueError(f"Unsupported artifact format: {fmt}")

    return ServiceGroupArtifact(
        format=fmt,
        filename=filename,
        content=content,
        service_group_name=service_group_name,
        display_name=display_name,
        member_count=len(members),
    )
