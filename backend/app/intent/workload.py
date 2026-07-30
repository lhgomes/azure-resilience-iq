from pydantic import BaseModel, Field, field_validator
from typing import Dict, List, Optional


class GraphViewport(BaseModel):
    x: float
    y: float
    zoom: float


class GraphViewState(BaseModel):
    viewport: Optional[GraphViewport] = None
    node_positions: Dict[str, Dict[str, float]] = Field(default_factory=dict)


class ServiceGroupFilter(BaseModel):
    """Authoritative membership of an imported Azure Service Group.

    Unlike the coarse subscription/resource-group/service filters, this pins the
    exact resource IDs that belong to the Service Group so the workload stays
    faithful to the source even when sibling resources share a resource group.
    """

    service_group_id: Optional[str] = None
    service_group_name: Optional[str] = None
    display_name: Optional[str] = None
    parent_service_group_id: Optional[str] = None
    member_resource_ids: List[str] = Field(default_factory=list)


class WorkloadViewState(BaseModel):
    selected_subscriptions: List[str] = Field(default_factory=list)
    view_level: str = "overview"
    ai_layer_enabled: bool = True
    user_layer_enabled: bool = True
    resource_group_filter: List[str] = Field(default_factory=list)
    service_filter: List[str] = Field(default_factory=list)
    expanded_categories: List[str] = Field(default_factory=list)
    show_legend: bool = False
    graph_view: Optional[GraphViewState] = None
    service_group_filter: Optional[ServiceGroupFilter] = None
    region_az_counts: Dict[str, int] = Field(default_factory=dict)

    @field_validator("region_az_counts", mode="before")
    @classmethod
    def validate_region_az_counts(cls, value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("region_az_counts must be an object")

        normalized: Dict[str, int] = {}
        for region_raw, az_count_raw in value.items():
            region_key = str(region_raw or "").strip().lower().replace(" ", "")
            if not region_key:
                continue

            try:
                az_count = int(az_count_raw)
            except (TypeError, ValueError):
                raise ValueError(f"Invalid AZ count for region '{region_raw}'")

            if az_count not in (1, 2, 3):
                raise ValueError(f"AZ count for region '{region_raw}' must be 1, 2, or 3")

            normalized[region_key] = az_count

        return normalized


class Workload(BaseModel):
    workload_id: str
    name: str
    view_state: WorkloadViewState
    created_at: str
    updated_at: str
    conversation_id: Optional[str] = None


class CreateWorkloadRequest(BaseModel):
    name: str
    view_state: WorkloadViewState


class UpdateWorkloadRequest(BaseModel):
    name: Optional[str] = None
    view_state: Optional[WorkloadViewState] = None
