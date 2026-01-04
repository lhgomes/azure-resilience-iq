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

  const strokeDasharray = 
    edgeData.status === "proposed"
      ? "5,5"
      : edgeData.origin === "heuristic"
      ? "3,3"
      : "none";

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
              whiteSpace: "nowrap"
            }}
            className="nodrag nopan"
          >
            {edgeData.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
};

export default AzureEdge;
