from pydantic import BaseModel


class ManualEdge(BaseModel):
    id: str
    from_id: str
    to_id: str
    relationship: str
    confidence: float = 1.0
    status: str = "accepted"
    source: str = "manual"
    created_by: str = "user"
