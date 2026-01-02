import { useEffect, useState } from "react";
import GraphCanvas from "../components/GraphCanvas";

export default function WorkloadView() {
  const [graph, setGraph] = useState({ nodes: [], edges: [] });

  useEffect(() => {
    fetch("http://localhost:8000/api/workloads/demo/graph")
      .then(r => r.json())
      .then(setGraph);
  }, []);

  return <GraphCanvas graph={graph} />;
}
