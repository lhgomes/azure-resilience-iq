from pydantic import BaseModel
from typing import Dict, Any, Optional


class AzureResource(BaseModel):
    id: str
    name: str
    type: str
    location: Optional[str]
    resource_group: str
    subscription_id: str
    tags: Dict[str, Any] = {}
    properties: Dict[str, Any] = {}
