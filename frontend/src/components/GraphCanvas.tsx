import Cytoscape from "cytoscape";
import { useEffect, useRef } from "react";

export default function GraphCanvas({ graph }: any) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;

    Cytoscape({
      container: ref.current,
      elements: [
        ...graph.nodes.map((n: any) => ({
          data: { id: n.id, label: n.name }
        })),
        ...graph.edges.map((e: any) => ({
          data: {
            id: e.id,
            source: e.from_id,
            target: e.to_id,
            label: e.relationship,
            confidence: e.confidence
          }
        }))
      ],
      style: [
        {
          selector: "node",
          style: {
            "background-color": "#0078D4",
            "label": "data(label)",
            "color": "#fff",
            "text-valign": "center",
            "font-size": "10px"
          }
        },
        {
          selector: "edge",
          style: {
            "width": 2,
            "line-color": "#a0a0a0",
            "target-arrow-color": "#a0a0a0",
            "target-arrow-shape": "triangle",
            "label": "data(label)",
            "font-size": "8px"
          }
        },
        {
          selector: "edge[confidence < 0.8]",
          style: {
            "line-style": "dashed"
          }
        }
      ],
      layout: {
        name: "breadthfirst",
        directed: true
      }
    });
  }, [graph]);

  return <div ref={ref} style={{ height: "100vh" }} />;
}
