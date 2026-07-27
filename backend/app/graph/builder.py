import uuid
from .model import Edge, EdgeStatus, Node
from app.intent.overrides import EdgeDecision
from app.storage.edge_overrides_store import load_overrides

# Namespace UUID for azure-resilience-iq edges
EDGE_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # DNS namespace


def edge_id(source: str, target: str, relationship: str) -> str:
    """Generate deterministic UUIDv5 for an edge based on source, relationship, and target."""
    raw = f"{source}|{relationship}|{target}"
    return str(uuid.uuid5(EDGE_NAMESPACE, raw))


class GraphBuilder:
    def __init__(self):
        self.nodes: dict[str, Node] = {}
        self.edges: dict[str, Edge] = {}

    def add_node(self, node: Node):
        self.nodes[node.id] = node

    def add_edge(
        self,
        source: str,
        target: str,
        relationship: str,
        origin: str,
        confidence: float,
        evidence: list[dict] | None = None,
        edge_id_override: str | None = None,
    ):
        eid = edge_id_override or edge_id(source, target, relationship)
        if eid not in self.edges:
            self.edges[eid] = Edge(
                id=eid,
                source=source,
                target=target,
                relationship=relationship,
                origin=origin,
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

