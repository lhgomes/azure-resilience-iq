from pydantic import BaseModel, Field
from typing import List, Optional


class WorkloadViewState(BaseModel):
    selected_subscriptions: List[str] = Field(default_factory=list)
    view_level: str = "overview"
    ai_layer_enabled: bool = True
    user_layer_enabled: bool = True
    resource_group_filter: List[str] = Field(default_factory=list)
    service_filter: List[str] = Field(default_factory=list)
    expanded_categories: List[str] = Field(default_factory=list)
    show_legend: bool = False


class Workload(BaseModel):
    workload_id: str
    name: str
    view_state: WorkloadViewState
    created_at: str
    updated_at: str


class CreateWorkloadRequest(BaseModel):
    name: str
    view_state: WorkloadViewState


class UpdateWorkloadRequest(BaseModel):
    name: Optional[str] = None
    view_state: Optional[WorkloadViewState] = None
