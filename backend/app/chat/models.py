"""Data models for chat service."""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class SuggestedEdge(BaseModel):
    """Edge suggestion from LLM."""
    source: str
    target: str
    relationship: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: Optional[str] = None


class CriticalityInsight(BaseModel):
    """Criticality analysis from LLM."""
    node_id: str
    suggested_score: int = Field(ge=1, le=10)
    reason: str
    current_score: Optional[int] = None


class ChatRequest(BaseModel):
    """Request for chat service."""
    message: str
    subscription_id: str
    context: Optional[Dict[str, Any]] = None
    conversation_history: Optional[List[Dict[str, str]]] = None


class ChatResponse(BaseModel):
    """Response from chat service."""
    message: str
    suggested_edges: List[SuggestedEdge] = Field(default_factory=list)
    resources_to_highlight: List[str] = Field(default_factory=list)
    criticality_insights: List[CriticalityInsight] = Field(default_factory=list)
    recommendations: List[Dict[str, Any]] = Field(default_factory=list)
    remediation_guide: Optional[Dict[str, Any]] = None
    terraform_code: Optional[str] = None
    terraform_validation: Optional[str] = None
    clarifying_questions: List[str] = Field(default_factory=list)
    raw_llm_output: Optional[Dict[str, Any]] = None


class ConversationMessage(BaseModel):
    """Message in conversation history."""
    role: str  # 'user' or 'assistant'
    content: str
    timestamp: Optional[str] = None
