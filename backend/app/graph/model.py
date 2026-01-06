from enum import Enum
from typing import Any, Dict, List
from pydantic import BaseModel, Field


class NodeState(str, Enum):
    detected = "detected"
    accepted = "accepted"
    excluded = "excluded"


class EdgeStatus(str, Enum):
    proposed = "proposed"
    accepted = "accepted"
    rejected = "rejected"


class Node(BaseModel):
    id: str
    type: str
    name: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    source: str
    state: NodeState = NodeState.detected


class Edge(BaseModel):
    id: str
    source: str  # edge start node
    target: str  # edge end node
    relationship: str
    confidence: float
    origin: str  # edge origin (arg/manual/heuristic/etc)
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    status: EdgeStatus = EdgeStatus.proposed


class Finding(BaseModel):
    id: str
    category: str
    severity: str
    title: str
    description: str
    affected_nodes: List[str] = Field(default_factory=list)
    affected_edges: List[str] = Field(default_factory=list)
    recommendation: str
    source: str
