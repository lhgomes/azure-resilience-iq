from __future__ import annotations

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel


# Preview API versions for the Service Groups resource types.
SERVICE_GROUP_API_VERSION = "2024-02-01-preview"
SERVICE_GROUP_MEMBER_API_VERSION = "2023-09-01-preview"


class ArtifactFormat(str, Enum):
    terraform = "terraform"
    arm = "arm"


class ServiceGroupMemberRef(BaseModel):
    """A resolved Azure resource that will become a Service Group member."""

    resource_id: str
    member_name: str
    display_name: Optional[str] = None
    azure_type: Optional[str] = None
    subscription_id: Optional[str] = None


class ServiceGroupSummary(BaseModel):
    """A Service Group discovered in Azure via Resource Graph (read path)."""

    id: str
    name: str
    display_name: str
    parent_service_group_id: Optional[str] = None


class ServiceGroupAvailability(BaseModel):
    """Whether the backend identity can read Service Groups at tenant scope.

    Gates the Service Group UI: ARG list queries silently trim unreadable
    results, so an empty list cannot prove access. This flag is derived from a
    definitive ARM GET probe on the tenant-root Service Group instead.
    """

    available: bool
    reason: Optional[str] = None


class ServiceGroupArtifact(BaseModel):
    """A downloadable IaC artifact that provisions the Service Group and its members."""

    format: ArtifactFormat
    filename: str
    content: str
    service_group_name: str
    display_name: str
    member_count: int


class ServiceGroupExportRequest(BaseModel):
    format: ArtifactFormat = ArtifactFormat.terraform


class ServiceGroupApplyRequest(BaseModel):
    # Format used for the fallback artifact returned when the backend identity
    # lacks permission to write to Azure.
    fallback_format: ArtifactFormat = ArtifactFormat.terraform


class ServiceGroupWorkloadApplyRequest(BaseModel):
    """Create or update a Service Group from a workload's current resources.

    ``service_group_name`` is set when the workload is already bound to a
    Service Group (update path). When absent, a deterministic name is derived
    from ``workload_id`` (create path). ``previous_member_resource_ids`` is the
    last-applied membership, used to detach resources dropped from the workload.
    ``parent_service_group_id`` is the user-selected parent for a NEW Service
    Group (an existing Service Group keeps its current parent); when omitted the
    parent defaults to the tenant root.
    """

    workload_id: str
    display_name: str
    member_resource_ids: List[str] = []
    previous_member_resource_ids: List[str] = []
    service_group_name: Optional[str] = None
    parent_service_group_id: Optional[str] = None
    fallback_format: ArtifactFormat = ArtifactFormat.terraform


class ApplyServiceGroupResult(BaseModel):
    # applied         -> Service Group and all members written successfully
    # deleted         -> Service Group and its member relationships removed
    # permission_denied -> backend identity lacks required rights; use `artifact`
    # error           -> a non-permission failure occurred; `artifact` provided
    # empty           -> the group has no Azure resource members to apply
    status: str
    service_group_name: Optional[str] = None
    service_group_id: Optional[str] = None
    display_name: Optional[str] = None
    parent_service_group_id: Optional[str] = None
    applied_members: List[str] = []
    detached_members: List[str] = []
    failed_members: List[str] = []
    artifact: Optional[ServiceGroupArtifact] = None
    message: Optional[str] = None
