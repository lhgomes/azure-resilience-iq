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
        width: 280,
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
          <strong>Node colors:</strong>
          <div style={{ marginTop: 4, color: "#9AA0A6" }}>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#0078D4" }}>⬤ Compute (AKS, VM)</span>
            </div>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#85a2c6ff" }}>⬤ Network (VNet, Subnet)</span>
            </div>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#00B294" }}>⬤ PaaS (SQL, Storage, KeyVault)</span>
            </div>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#16a34a" }}>⬤ Manual (user-created)</span>
            </div>
          </div>
        </div>

        <div style={{ marginBottom: 12 }}>
          <strong>Edge styles:</strong>
          <div style={{ marginTop: 4, color: "#9AA0A6" }}>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#0078D4" }}>━ Solid blue: ARG (Azure)</span>
            </div>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#16a34a" }}>━ Solid green: Manual</span>
            </div>
            <div style={{ marginBottom: 3 }}>╌ Dashed: Heuristic</div>
          </div>
        </div>

        <div style={{ marginBottom: 12 }}>
          <strong>View levels:</strong>
          <div style={{ marginTop: 4, color: "#9AA0A6" }}>
            <div style={{ marginBottom: 3 }}>L0: Core workload</div>
            <div style={{ marginBottom: 3 }}>L1: Network + Platform</div>
            <div style={{ marginBottom: 3 }}>L2: Full + Implementation</div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default LegendPanel;
