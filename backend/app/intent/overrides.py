from enum import Enum
from typing import Optional
from pydantic import BaseModel


class EdgeDecision(str, Enum):
    accepted = "accepted"
    rejected = "rejected"


class EdgeOverride(BaseModel):
    edge_id: str
    decision: EdgeDecision
    reason: Optional[str] = None
    created_by: str = "user"
