from typing import Optional
from pydantic import BaseModel


class NodeOverride(BaseModel):
    node_id: str
    name: Optional[str] = None
    layer: Optional[int] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    group_id: Optional[str] = None
    group_label: Optional[str] = None
    criticality_score: Optional[int] = None
    hidden: Optional[bool] = None
    created_by: str = "user"
