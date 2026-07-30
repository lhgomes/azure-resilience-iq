from __future__ import annotations

from typing import List, Tuple

from app.storage.groups_store import get_group, NodeGroup
from app.services.workloads import build_workload_snapshot
from app.relationships.utils import is_azure_resource_id

from .models import (
    ApplyServiceGroupResult,
    ArtifactFormat,
    ServiceGroupArtifact,
    ServiceGroupAvailability,
    ServiceGroupMemberRef,
    ServiceGroupSummary,
)
from .naming import member_name, service_group_name
from .generator import generate_artifact
from .applier import apply_service_group, delete_service_group as _delete_service_group_azure
from .applier import check_service_group_read_access
from .reader import list_service_groups, list_service_group_member_ids


class GroupNotFoundError(Exception):
    pass


def list_available_service_groups() -> List[ServiceGroupSummary]:
    """Discover Service Groups in the caller's tenant (read path)."""
    return [ServiceGroupSummary(**sg) for sg in list_service_groups()]


def get_service_group_availability() -> ServiceGroupAvailability:
    """Report whether the backend identity can read Service Groups.

    Used to gate the Service Group UI: read requires a grant at the tenant-root
    Service Group scope that a standard deploy principal cannot self-assign.
    """
    access = check_service_group_read_access()
    return ServiceGroupAvailability(available=access.available, reason=access.reason)


def get_service_group_member_ids(service_group_name: str) -> List[str]:
    """Return the normalized resource IDs belonging to a Service Group."""
    return list_service_group_member_ids(service_group_name)


def delete_service_group(service_group_name: str) -> ApplyServiceGroupResult:
    """Delete a Service Group and its member relationships from Azure.

    Used when a bound workload is removed and the user opts to also delete the
    backing Azure Service Group.
    """
    outcome = _delete_service_group_azure(service_group_name)

    if outcome.permission_denied:
        return ApplyServiceGroupResult(
            status="permission_denied",
            service_group_name=service_group_name,
            detached_members=outcome.detached,
            failed_members=outcome.failed,
            message=(
                outcome.error_message
                or "The backend identity lacks permission to delete Service Groups. "
                "Delete it with an identity that has Service Group Contributor rights."
            ),
        )

    if outcome.error_message or outcome.failed:
        return ApplyServiceGroupResult(
            status="error",
            service_group_name=service_group_name,
            detached_members=outcome.detached,
            failed_members=outcome.failed,
            message=outcome.error_message or "The Service Group could not be deleted.",
        )

    removed = len(outcome.detached)
    return ApplyServiceGroupResult(
        status="deleted",
        service_group_name=service_group_name,
        detached_members=outcome.detached,
        message=f"Service Group deleted ({removed} member{'' if removed == 1 else 's'} detached).",
    )


def _resolve_members(subscription_id: str, group: NodeGroup) -> List[ServiceGroupMemberRef]:
    """Map a group's node ids to real, non-virtual Azure resources."""
    snapshot = build_workload_snapshot(subscription_id)
    nodes_by_id: dict = {}
    for node in snapshot.get("nodes") or []:
        node_dict = node if isinstance(node, dict) else (
            node.model_dump() if hasattr(node, "model_dump") else None
        )
        if node_dict and node_dict.get("id"):
            nodes_by_id[node_dict["id"]] = node_dict

    members: List[ServiceGroupMemberRef] = []
    seen: set = set()
    for node_id in group.nodes:
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        if not is_azure_resource_id(node_id):
            continue
        node = nodes_by_id.get(node_id)
        meta = (node or {}).get("metadata") or {}
        if meta.get("virtual"):
            continue
        members.append(
            ServiceGroupMemberRef(
                resource_id=node_id,
                member_name=member_name(node_id),
                display_name=meta.get("display_name") or meta.get("label"),
                azure_type=meta.get("azure_type"),
                subscription_id=meta.get("subscription_id") or subscription_id,
            )
        )
    return members


def build_service_group_context(
    subscription_id: str, group_id: str
) -> Tuple[NodeGroup, str, str, List[ServiceGroupMemberRef]]:
    group = get_group(subscription_id, group_id)
    if group is None:
        raise GroupNotFoundError(group_id)
    members = _resolve_members(subscription_id, group)
    sg_name = service_group_name(group.id)
    display_name = group.name or sg_name
    return group, sg_name, display_name, members


def export_service_group(
    subscription_id: str, group_id: str, fmt: ArtifactFormat
) -> ServiceGroupArtifact:
    _, sg_name, display_name, members = build_service_group_context(subscription_id, group_id)
    return generate_artifact(fmt, sg_name, display_name, members)


def apply_service_group_for_group(
    subscription_id: str, group_id: str, fallback_format: ArtifactFormat
) -> ApplyServiceGroupResult:
    _, sg_name, display_name, members = build_service_group_context(subscription_id, group_id)

    if not members:
        return ApplyServiceGroupResult(
            status="empty",
            service_group_name=sg_name,
            display_name=display_name,
            message="This group has no Azure resource members to add to a Service Group.",
        )

    outcome = apply_service_group(sg_name, display_name, members)

    if outcome.permission_denied:
        return ApplyServiceGroupResult(
            status="permission_denied",
            service_group_name=sg_name,
            display_name=display_name,
            applied_members=outcome.applied,
            failed_members=outcome.failed,
            artifact=generate_artifact(fallback_format, sg_name, display_name, members),
            message=(
                outcome.error_message
                or "The backend identity lacks permission to write Service Groups. "
                "Download the generated template and apply it with an identity that "
                "has Service Group Contributor and Microsoft.Relationship/write."
            ),
        )

    if outcome.failed:
        return ApplyServiceGroupResult(
            status="error",
            service_group_name=sg_name,
            display_name=display_name,
            applied_members=outcome.applied,
            failed_members=outcome.failed,
            artifact=generate_artifact(fallback_format, sg_name, display_name, members),
            message=outcome.error_message or "One or more members could not be applied.",
        )

    return ApplyServiceGroupResult(
        status="applied",
        service_group_name=sg_name,
        display_name=display_name,
        applied_members=outcome.applied,
        message=f"Service Group '{display_name}' applied with {len(outcome.applied)} member(s).",
    )


def apply_service_group_for_workload(
    *,
    workload_id: str,
    display_name: str,
    member_resource_ids: List[str],
    previous_member_resource_ids: List[str],
    existing_service_group_name: str | None,
    fallback_format: ArtifactFormat,
    parent_service_group_id: str | None = None,
) -> ApplyServiceGroupResult:
    """Create or update a Service Group from a workload's current resources.

    Desired members are the workload's current Azure resources; members present
    in ``previous_member_resource_ids`` but no longer desired are detached
    (full sync). The Service Group name is reused when the workload is already
    bound, otherwise derived deterministically from the workload id.

    ``parent_service_group_id`` sets the parent only for a NEW Service Group
    (tenant root when omitted); an existing Service Group keeps its own parent.
    """
    desired_ids: List[str] = []
    seen: set = set()
    for rid in member_resource_ids:
        if rid and rid not in seen and is_azure_resource_id(rid):
            seen.add(rid)
            desired_ids.append(rid)
    desired_set = set(desired_ids)

    detach_ids = [
        rid
        for rid in (previous_member_resource_ids or [])
        if is_azure_resource_id(rid) and rid not in desired_set
    ]

    sg_name = existing_service_group_name or service_group_name(workload_id)
    disp = display_name or sg_name
    sg_id = f"/providers/Microsoft.Management/serviceGroups/{sg_name}"
    resolved_parent = parent_service_group_id

    if not desired_ids and not detach_ids:
        return ApplyServiceGroupResult(
            status="empty",
            service_group_name=sg_name,
            service_group_id=sg_id,
            display_name=disp,
            parent_service_group_id=resolved_parent,
            message="This workload has no Azure resources to include in a Service Group.",
        )

    members = [
        ServiceGroupMemberRef(resource_id=rid, member_name=member_name(rid))
        for rid in desired_ids
    ]

    outcome = apply_service_group(
        sg_name,
        disp,
        members,
        detach_resource_ids=detach_ids,
        parent_service_group_id=parent_service_group_id,
        prune_to_members=True,
    )

    resolved_parent = outcome.resolved_parent_service_group_id or resolved_parent

    if outcome.permission_denied:
        return ApplyServiceGroupResult(
            status="permission_denied",
            service_group_name=sg_name,
            service_group_id=sg_id,
            display_name=disp,
            parent_service_group_id=resolved_parent,
            applied_members=outcome.applied,
            detached_members=outcome.detached,
            failed_members=outcome.failed,
            artifact=generate_artifact(fallback_format, sg_name, disp, members, parent_service_group_id),
            message=(
                outcome.error_message
                or "The backend identity lacks permission to write Service Groups. "
                "Download the generated template and apply it with an identity that "
                "has Service Group Contributor and Microsoft.Relationship/write."
            ),
        )

    if outcome.failed or (outcome.error_message and not outcome.applied and not outcome.detached):
        return ApplyServiceGroupResult(
            status="error",
            service_group_name=sg_name,
            service_group_id=sg_id,
            display_name=disp,
            parent_service_group_id=resolved_parent,
            applied_members=outcome.applied,
            detached_members=outcome.detached,
            failed_members=outcome.failed,
            artifact=generate_artifact(fallback_format, sg_name, disp, members, parent_service_group_id),
            message=outcome.error_message or "One or more members could not be applied.",
        )

    return ApplyServiceGroupResult(
        status="applied",
        service_group_name=sg_name,
        service_group_id=sg_id,
        display_name=disp,
        parent_service_group_id=resolved_parent,
        applied_members=outcome.applied,
        detached_members=outcome.detached,
        message=(
            f"Service Group '{disp}' updated: {len(outcome.applied)} member(s) attached"
            + (f", {len(outcome.detached)} removed." if outcome.detached else ".")
        ),
    )
