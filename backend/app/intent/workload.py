from pydantic import BaseModel, Field
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
