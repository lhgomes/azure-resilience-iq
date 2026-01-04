import hashlib
from .model import Edge, EdgeStatus, Node
from app.intent.overrides import EdgeDecision
from app.storage.overrides_store import load_overrides


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

    def build(self, workload_id: str):
        overrides = load_overrides(workload_id)

        for edge_id, edge in self.edges.items():
            override = overrides.get(edge_id)
            if not override:
                continue

            if override.decision == EdgeDecision.accepted:
                edge.status = EdgeStatus.accepted

            elif override.decision == EdgeDecision.rejected:
                edge.status = EdgeStatus.rejected

        return {
            "nodes": list(self.nodes.values()),
            # hide rejected edges by default
            "edges": [
                e for e in self.edges.values()
                if e.status != EdgeStatus.rejected
            ],
        }

