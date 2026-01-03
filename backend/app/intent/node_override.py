from typing import Optional
from pydantic import BaseModel


class NodeOverride(BaseModel):
    node_id: str
    name: Optional[str] = None
    layer: Optional[int] = None
    shape: Optional[str] = None
    color: Optional[str] = None
    created_by: str = "user"
