from pydantic import BaseModel


class ManualEdge(BaseModel):
    """Manual edge aligned with ReactFlow naming (source/target)."""

    id: str
    source: str
    target: str
    relationship: str
    confidence: float = 1.0
    status: str = "accepted"
    origin: str = "manual"
    created_by: str = "user"
