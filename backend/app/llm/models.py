from typing import Any, List, Optional
from pydantic import BaseModel, Field


class NodeAnnotationPayload(BaseModel):
    """Advisory per-node annotations returned by an LLM (non-authoritative)."""

    display_name: Optional[str] = None
    azure_service_category: Optional[str] = None
    azure_service_name: Optional[str] = None
    layer: Optional[int] = None
    priority: Optional[str] = None
    criticality_score: Optional[int] = Field(default=None, ge=1, le=10)
    hide_by_default: Optional[bool] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    reason: Optional[str] = None
    source: str = "llm"


class EdgeSuggestionPayload(BaseModel):
    """Advisory edge suggestion between two existing nodes (non-authoritative)."""

    source: str
    target: str
    relationship: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    reason: Optional[str] = None
    status: Optional[str] = "proposed"
    origin: str = "llm"
    id: Optional[str] = None


class NodeAnnotation(BaseModel):
    """Wrapper linking an advisory annotation payload to a node id."""

    node_id: str
    annotations: NodeAnnotationPayload


class LLMAnnotations(BaseModel):
    """Container for all advisory LLM outputs; never authoritative."""

    nodes: List[NodeAnnotation] = Field(default_factory=list)
    edges: List[EdgeSuggestionPayload] = Field(default_factory=list)
