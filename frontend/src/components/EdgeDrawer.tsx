import React from "react";

export interface EdgeData {
  id: string;
  source: string;
  target: string;
  relationship: string;
  confidence?: number;
  status?: "proposed" | "accepted" | "rejected";
  evidence?: any[];
  origin?: string;
  raw?: any;
}

interface Props {
  edge: EdgeData | null;
  onAccept?: (edgeId: string) => void;
  onReject?: (edgeId: string) => void;
  onDelete?: (edgeId: string) => void;
  onClose?: () => void;
}

const EdgeDrawer: React.FC<Props> = ({
  edge,
  onAccept,
  onReject,
  onDelete,
  onClose
}) => {
  if (!edge) return null;

  const confidencePct =
    edge.confidence !== undefined
      ? Math.round(edge.confidence * 100)
      : undefined;

  const aiSuggested = edge.origin === "llm";
  const userCustomized = edge.origin === "manual" || edge.status === "accepted" || edge.status === "rejected";
  const rawJson = edge.raw ? JSON.stringify(edge.raw, null, 2) : null;

  return (
    <div
      style={{
        width: 360,
        height: "100%",
        background: "#111",
        color: "#fff",
        borderLeft: "1px solid #333",
        padding: 16,
        boxSizing: "border-box",
        position: "relative",
        overflowY: "auto"
      }}
    >
      {/* Close button */}
      <button
        onClick={onClose}
        style={{
          position: "absolute",
          top: 12,
          right: 12,
          background: "transparent",
          color: "#aaa",
          border: "none",
          fontSize: 16,
          cursor: "pointer"
        }}
        title="Close"
      >
        ✕
      </button>

      {/* Header */}
      <h3 style={{ marginTop: 0, marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
        Dependency
        {aiSuggested && (
          <span
            title="AI-suggested"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: "2px 6px",
              borderRadius: 12,
              background: "#2f1f08",
              color: "#f59e0b",
              fontSize: 11,
              fontWeight: 600,
              border: "1px solid #f59e0b"
            }}
          >
            AI
          </span>
        )}
        {userCustomized && (
          <span
            title="User input"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: "2px 6px",
              borderRadius: 12,
              background: "#1e4620",
              color: "#ffffff",
              fontSize: 11,
              fontWeight: 600,
              border: "1px solid #2ea043"
            }}
          >
            Ui
          </span>
        )}
      </h3>

      {/* Relationship info */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ marginBottom: 6 }}>
          <strong>Type</strong>
        </div>
        <div style={{ color: "#9CDCFE" }}>
          {edge.relationship}
        </div>
      </div>

      <div style={{ marginBottom: 16 }}>
        <div style={{ marginBottom: 6 }}>
          <strong>Source</strong>
        </div>
        <div style={{ fontSize: 12, color: "#ccc" }}>
          {edge.source}
        </div>
      </div>

      <div style={{ marginBottom: 16 }}>
        <div style={{ marginBottom: 6 }}>
          <strong>Target</strong>
        </div>
        <div style={{ fontSize: 12, color: "#ccc" }}>
          {edge.target}
        </div>
      </div>

      <div style={{ marginBottom: 16 }}>
        <div style={{ marginBottom: 6 }}>
          <strong>Origin</strong>
        </div>
        <div style={{ fontSize: 12, color: "#9AA0A6" }}>
          {edge.origin ?? "unknown"}
        </div>
      </div>

      {aiSuggested && (
        <div
          style={{
            marginBottom: 16,
            padding: 10,
            border: "1px dashed #444",
            background: "#1b1b1b",
            color: "#e5e5e5",
            fontSize: 12,
          }}
          title="Suggested by LLM; non-authoritative"
        >
          AI-suggested edge (not authoritative)
        </div>
      )}

      {/* Confidence */}
      {confidencePct !== undefined && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ marginBottom: 6 }}>
            <strong>Confidence</strong>
          </div>
          <div
            style={{
              color:
                confidencePct >= 90
                  ? "#4CAF50"
                  : confidencePct >= 70
                  ? "#FFC107"
                  : "#F44336"
            }}
          >
            {confidencePct}%
          </div>
        </div>
      )}

      {/* Status */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ marginBottom: 6 }}>
          <strong>Status</strong>
        </div>
        <div
          style={{
            color:
              edge.status === "accepted"
                ? "#4CAF50"
                : edge.status === "rejected"
                ? "#F44336"
                : "#FFC107"
          }}
        >
          {edge.status ?? "proposed"}
        </div>
      </div>

      {/* Actions */}
      <div
        style={{
          display: "flex",
          gap: 12,
          marginTop: "auto"
        }}
      >
        <button
          onClick={() => onAccept?.(edge.id)}
          disabled={edge.status === "accepted" || aiSuggested}
          style={{
            flex: 1,
            padding: "8px 12px",
            background:
              edge.status === "accepted" || aiSuggested ? "#1e4620" : "#2ea043",
            color: "#fff",
            border: "none",
            cursor: "pointer",
            opacity: edge.status === "accepted" || aiSuggested ? 0.6 : 1
          }}
        >
          ✓ Accept
        </button>

        <button
          onClick={() => onReject?.(edge.id)}
          disabled={edge.status === "rejected" || aiSuggested}
          style={{
            flex: 1,
            padding: "8px 12px",
            background:
              edge.status === "rejected" || aiSuggested ? "#4c1d1d" : "#d73a49",
            color: "#fff",
            border: "none",
            cursor: "pointer",
            opacity: edge.status === "rejected" || aiSuggested ? 0.6 : 1
          }}
        >
          ✕ Reject
        </button>

        {edge.origin === "manual" && (
          <button
            onClick={() => onDelete?.(edge.id)}
            style={{
              flex: 1,
              padding: "8px 12px",
              background: "#312e81",
              color: "#fff",
              border: "none",
              cursor: "pointer"
            }}
          >
            Delete
          </button>
        )}
      </div>

      {rawJson && (
        <details style={{ marginTop: 16 }}>
          <summary style={{ cursor: "pointer", color: "#9AA0A6" }}>
            Raw edge data
          </summary>
          <pre
            style={{
              background: "#0b0b0b",
              padding: 12,
              border: "1px solid #222",
              color: "#ddd",
              overflowX: "auto"
            }}
          >
            {rawJson}
          </pre>
        </details>
      )}
    </div>
  );
};

export default EdgeDrawer;