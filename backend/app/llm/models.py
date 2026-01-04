from typing import Any, Dict, List, Optional
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

    from_id: str
    to_id: str
    relationship: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    reason: Optional[str] = None
    status: Optional[str] = "proposed"
    source: str = "llm"


class NodeAnnotation(BaseModel):
    """Wrapper linking an advisory annotation payload to a node id."""

    node_id: str
    annotations: NodeAnnotationPayload


class LLMAnnotations(BaseModel):
    """Container for all advisory LLM outputs; never authoritative."""

    nodes: List[NodeAnnotation] = Field(default_factory=list)
    edges: List[EdgeSuggestionPayload] = Field(default_factory=list)


class LLMSafeNode(BaseModel):
    """Internal safe node representation used for prompt construction."""

    alias: str
    type: str
    name: str
    importance: int | None = None
    connections: List[str] = Field(default_factory=list)


class LLMSafeEdge(BaseModel):
    """Internal safe edge representation used for prompt construction."""

    source: str
    target: str
    relationship: str


class LLMSummary(BaseModel):
    """Internal summary passed to the LLM; excludes noisy metadata."""

    nodes: List[LLMSafeNode]
    edges: List[LLMSafeEdge]
    alias_to_node_id: Dict[str, str]
