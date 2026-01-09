import React from "react";

interface Props {
  open: boolean;
  onClose: () => void;
}

const LegendPanel: React.FC<Props> = ({ open, onClose }) => {
  if (!open) return null;

  return (
    <div
      style={{
        position: "fixed",
        top: 60,
        right: 20,
        width: 320,
        maxHeight: "calc(100vh - 100px)",
        overflowY: "auto",
        background: "#111",
        border: "1px solid #333",
        borderRadius: 6,
        padding: 16,
        color: "#eee",
        zIndex: 100,
        boxShadow: "0 4px 12px rgba(0,0,0,0.5)",
      }}
    >
      <div style={{ marginBottom: 16 }}>
        <h4 style={{ marginTop: 0, marginBottom: 8 }}>Visual Legend</h4>
        <button
          onClick={onClose}
          style={{
            position: "absolute",
            top: 8,
            right: 8,
            background: "transparent",
            border: "none",
            color: "#9AA0A6",
            cursor: "pointer",
            fontSize: 16,
          }}
        >
          ✕
        </button>
      </div>

      <div style={{ fontSize: 12, lineHeight: 1.6 }}>
        <div style={{ marginBottom: 12 }}>
          <strong>Node Header Colors (Criticality):</strong>
          <div style={{ marginTop: 4, color: "#9AA0A6" }}>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#b7b8baff" }}>⬤ Gray: Azure-managed (no APRL)</span>
            </div>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#22c55e" }}>⬤ Green: Low (1-2/10)</span>
            </div>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#84cc16" }}>⬤ Lime: Low-Med (3-4/10)</span>
            </div>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#eab308" }}>⬤ Yellow: Medium (5-6/10)</span>
            </div>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#f97316" }}>⬤ Orange: High (7-8/10)</span>
            </div>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#ef4444" }}>⬤ Red: Critical (9-10/10)</span>
            </div>
          </div>
        </div>

        <div style={{ marginBottom: 12 }}>
          <strong>Edge Colors:</strong>
          <div style={{ marginTop: 4, color: "#9AA0A6" }}>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#5EA0EF" }}>━ Blue: ARG (Azure Resource Graph)</span>
            </div>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#22c55e" }}>━ Green: Manual (user-created)</span>
            </div>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#f59e0b" }}>━ Orange: AI suggested</span>
            </div>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#9AA0A6" }}>╌ Gray dashed: Heuristic</span>
            </div>
          </div>
        </div>

        <div style={{ marginBottom: 12 }}>
          <strong>Node Metrics:</strong>
          <div style={{ marginTop: 4, color: "#9AA0A6" }}>
            <div style={{ marginBottom: 3 }}>◎ Confidence (AI analysis)</div>
            <div style={{ marginBottom: 3 }}>⚡ Criticality score (x/10)</div>
          </div>
        </div>

        <div style={{ marginBottom: 12 }}>
          <strong>Badges:</strong>
          <div style={{ marginTop: 4, color: "#9AA0A6" }}>
            <div style={{ marginBottom: 3 }}>
              <span style={{ 
                background: "#f59e0b", 
                color: "#000", 
                padding: "1px 4px", 
                borderRadius: 4,
                fontSize: 9,
                fontWeight: 700
              }}>AI</span> AI-enhanced node
            </div>
            <div style={{ marginBottom: 3 }}>
              <span style={{ 
                background: "#2ea043", 
                color: "#fff", 
                padding: "1px 4px", 
                borderRadius: 4,
                fontSize: 9,
                fontWeight: 700
              }}>Ui</span> User customized
            </div>
          </div>
        </div>

        <div style={{ marginBottom: 0 }}>
          <strong>APRL Coverage:</strong>
          <div style={{ marginTop: 4, color: "#9AA0A6", fontSize: 11 }}>
            Services covered by Azure Proactive Resiliency Library show criticality colors. Non-covered services (Azure-managed) show gray.
          </div>
        </div>
      </div>
    </div>
  );
};

export default LegendPanel;
