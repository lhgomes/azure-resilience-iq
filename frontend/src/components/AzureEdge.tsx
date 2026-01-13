import React from "react";
import { BaseEdge, EdgeLabelRenderer, getStraightPath, EdgeProps } from "reactflow";

const AzureEdge: React.FC<EdgeProps> = ({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  selected,
  markerEnd
}) => {
  const [edgePath, labelX, labelY] = getStraightPath({
    sourceX,
    sourceY,
    targetX,
    targetY
  });

  const edgeData = data as any || {};

  const getEdgeColor = (): string => {
    if (edgeData.origin === "manual") return "#22c55e";
    if (edgeData.origin === "llm") return "#f59e0b";
    if (edgeData.origin === "heuristic") return "#9AA0A6";
    return "#5EA0EF"; // ARG edges
  };

  // Manual accepted edges should be solid, others use dash patterns
  const strokeDasharray = 
    edgeData.status === "proposed"
      ? "5,5"
      : edgeData.origin === "heuristic"
      ? "3,3"
      : edgeData.origin === "manual" && edgeData.status === "accepted"
      ? "none"  // Solid line for accepted manual edges
      : "none";  // Solid line for all other accepted edges

  const labelTitleParts: string[] = [];
  if (edgeData.user_customized) {
    if (edgeData.origin === "manual") labelTitleParts.push("User input: manual edge");
    if (edgeData.status === "accepted") labelTitleParts.push("User input: accepted");
    if (edgeData.status === "rejected") labelTitleParts.push("User input: rejected");
  }
  if (edgeData.origin === "llm") labelTitleParts.push("AI suggested");
  const labelTitle = labelTitleParts.join(" • ");

  const color = getEdgeColor();

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        style={{
          stroke: color,
          strokeWidth: selected ? 3 : 2.2,
          strokeDasharray,
          opacity: 0.88,
          filter: selected ? "drop-shadow(0 0 4px rgba(245, 158, 11, 0.6))" : "none",
          transition: "all 0.2s ease"
        }}
      />
      {edgeData.label && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: "absolute",
              left: `${labelX}px`,
              top: `${labelY}px`,
              transform: `translate(-50%, -50%)`,
              background: "#1a1a1a",
              color: "#eee",
              padding: "2px 5px",
              borderRadius: "3px",
              fontSize: "9px",
              fontWeight: 500,
              pointerEvents: "all",
              border: `1px solid ${color}`,
              whiteSpace: "nowrap",
              zIndex: 10
            }}
            className="nodrag nopan"
            title={labelTitle || undefined}
          >
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <span>{edgeData.label}</span>
              {edgeData.origin === "llm" && (
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    padding: "1px 5px",
                    borderRadius: 999,
                    background: "#2f1f08",
                    color: "#f59e0b",
                    border: "1px solid #f59e0b",
                    fontSize: "8px",
                    fontWeight: 700,
                  }}
                  title="AI suggested"
                >
                  AI
                </span>
              )}
              {edgeData.user_customized && (
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    padding: "1px 5px",
                    borderRadius: 999,
                    background: "#1e4620",
                    color: "#fff",
                    border: "1px solid #2ea043",
                    fontSize: "8px",
                    fontWeight: 700,
                  }}
                  title="User input"
                >
                  Ui
                </span>
              )}
            </span>
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
};

export default AzureEdge;
