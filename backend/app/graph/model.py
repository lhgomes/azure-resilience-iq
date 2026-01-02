from enum import Enum
from typing import Any, Dict, List
from pydantic import BaseModel


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
    metadata: Dict[str, Any] = {}
    source: str
    state: NodeState = NodeState.detected


class Edge(BaseModel):
    id: str
    from_id: str
    to_id: str
    relationship: str
    confidence: float
    source: str
    evidence: List[Dict[str, Any]] = []
    status: EdgeStatus = EdgeStatus.proposed


class Finding(BaseModel):
    id: str
    category: str
    severity: str
    title: str
    description: str
    affected_nodes: List[str] = []
    affected_edges: List[str] = []
    recommendation: str
    source: str
