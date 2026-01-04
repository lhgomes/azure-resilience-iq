from typing import Optional
from pydantic import BaseModel


class NodeOverride(BaseModel):
    node_id: str
    name: Optional[str] = None
    layer: Optional[int] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    created_by: str = "user"
