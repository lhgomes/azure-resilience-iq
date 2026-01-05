import React from "react";
import { Handle, Position } from "reactflow";

interface AzureGroupNodeProps {
  data: {
    label: string;
    count: number;
    collapsed: boolean;
    onToggleCollapsed?: () => void;
  };
  selected: boolean;
}

const AzureGroupNode: React.FC<AzureGroupNodeProps> = ({ data, selected }) => {
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        borderRadius: 10,
        border: selected ? "2px solid #f59e0b" : "2px dashed #374151",
        background: "rgba(17, 24, 39, 0.15)",
        boxSizing: "border-box",
        position: "relative",
        cursor: "pointer",
        overflow: "hidden",
        pointerEvents: "none",
      }}
      title="Use the button to collapse/expand"
    >
      <Handle id="t" type="target" position={Position.Top} isConnectable={false} style={{ opacity: 0 }} />
      <Handle id="b" type="source" position={Position.Bottom} isConnectable={false} style={{ opacity: 0 }} />
      <Handle id="l" type="target" position={Position.Left} isConnectable={false} style={{ opacity: 0 }} />
      <Handle id="r" type="source" position={Position.Right} isConnectable={false} style={{ opacity: 0 }} />

      <div
        style={{
          position: "absolute",
          top: 4,
          left: 4,
          right: 10,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 10,
          fontSize: 12,
          fontWeight: 700,
          color: "#e5e7eb",
          pointerEvents: "auto",
          zIndex: 0,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <button
            type="button"
            className="nodrag nopan"
            onMouseDown={e => e.stopPropagation()}
            onClick={e => {
              e.stopPropagation();
              data.onToggleCollapsed?.();
            }}
            aria-label={data.collapsed ? "Expand group" : "Collapse group"}
            title={data.collapsed ? "Expand" : "Collapse"}
            style={{
              width: 22,
              height: 22,
              borderRadius: 6,
              border: "1px solid #374151",
              background: "rgba(17, 24, 39, 0.6)",
              color: "#e5e7eb",
              fontWeight: 900,
              fontSize: 12,
              lineHeight: "20px",
              padding: 0,
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
            }}
          >
            {data.collapsed ? "▸" : "▾"}
          </button>

          <div
            style={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              pointerEvents: "none",
            }}
          >
           <span style={{color: "#272727ff"}}>{data.label}</span>
          </div>
        </div>
        <div style={{ fontSize: 11, color: "#272727ff", fontWeight: 600, pointerEvents: "none" }}>
          {data.count} {data.count === 1 ? "node" : "nodes"} {data.collapsed ? "(collapsed)" : ""}
        </div>
      </div>
    </div>
  );
};

export default AzureGroupNode;
