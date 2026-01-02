from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.graph.builder import GraphBuilder
from app.graph.model import Node
from app.storage.json_store import load_snapshot

app = FastAPI(title="Azure Workload Graph")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/workloads/{workload_id}/graph")
def get_graph(workload_id: str):
    snapshot = load_snapshot(workload_id)
    if snapshot:
        return snapshot

    gb = GraphBuilder()

    gb.add_node(Node(
        id="aks-1",
        type="aks",
        name="payments-aks",
        source="arg"
    ))

    gb.add_node(Node(
        id="sql-1",
        type="sql",
        name="payments-sql",
        source="arg"
    ))

    gb.add_edge(
        from_id="aks-1",
        to_id="sql-1",
        relationship="depends_on",
        source="heuristic",
        confidence=0.6,
    )

    return gb.build()
