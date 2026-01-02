import hashlib
from .model import Node, Edge


def edge_id(from_id: str, to_id: str, relationship: str) -> str:
    raw = f"{from_id}|{relationship}|{to_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


class GraphBuilder:
    def __init__(self):
        self.nodes: dict[str, Node] = {}
        self.edges: dict[str, Edge] = {}

    def add_node(self, node: Node):
        self.nodes[node.id] = node

    def add_edge(
        self,
        from_id: str,
        to_id: str,
        relationship: str,
        source: str,
        confidence: float,
        evidence: list[dict] | None = None,
    ):
        eid = edge_id(from_id, to_id, relationship)
        if eid not in self.edges:
            self.edges[eid] = Edge(
                id=eid,
                from_id=from_id,
                to_id=to_id,
                relationship=relationship,
                source=source,
                confidence=confidence,
                evidence=evidence or [],
            )

    def build(self):
        return {
            "nodes": list(self.nodes.values()),
            "edges": list(self.edges.values()),
        }
