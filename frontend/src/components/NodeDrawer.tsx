import React, { useEffect, useState } from "react";

export interface NodeData {
  id: string;
  name: string;
  type?: string;
  layer?: number;
  shape?: string;
  color?: string;
  criticalityScore?: number;
  criticalityOverride?: boolean;
  override?: boolean;
  aiAnnotation?: AiAnnotation;
  originalName?: string;
  raw?: any;
}

interface AiAnnotation {
  display_name?: string;
  layer?: number;
  priority?: string;
  criticality_score?: number;
  hide_by_default?: boolean;
  confidence?: number;
  reason?: string;
  source?: string;
}

interface Props {
  node: NodeData | null;
  onClose?: () => void;
  onSave?: (nodeId: string, payload: { name?: string; layer?: number | null; shape?: string | null; color?: string | null; }) => void;
  onSaveCriticality?: (nodeId: string, score: number) => void;
  onResetCriticality?: (nodeId: string) => void;
  onReset?: () => void;
}

const SHAPES = [
  { value: "round-rectangle", label: "Round rectangle" },
  { value: "rectangle", label: "Rectangle" },
  { value: "ellipse", label: "Ellipse" },
  { value: "diamond", label: "Diamond" },
];

const NodeDrawer: React.FC<Props> = ({ node, onClose, onSave, onReset, onSaveCriticality, onResetCriticality }) => {
  const [name, setName] = useState("");
  const [layer, setLayer] = useState<number | "">("");
  const [shape, setShape] = useState<string>("");
  const [color, setColor] = useState<string>("");
  const [criticality, setCriticality] = useState<number | "">("");

  useEffect(() => {
    if (!node) return;
    setName(node.name ?? "");
    setLayer(typeof node.layer === "number" ? node.layer : "");
    setShape(node.shape ?? "");
    setColor(node.color ?? "");
    setCriticality(typeof node.criticalityScore === "number" ? node.criticalityScore : "");
  }, [node]);

  if (!node) return null;

  const baselineName = node.originalName ?? node.name;
  const baselineLayer = (node.raw as any)?.metadata?.original_importance ?? (node.raw as any)?.metadata?.importance ?? node.layer;
  const baselineShape = (node.raw as any)?.metadata?.shape_override ?? "";
  const baselineColor = (node.raw as any)?.metadata?.color_override ?? "";
  const baselineCriticality = node.aiAnnotation?.criticality_score ?? null;
  const effectiveCriticality = criticality === "" ? null : Number(criticality);
  const criticalityChanged = effectiveCriticality !== null
    ? (baselineCriticality === null ? true : effectiveCriticality !== baselineCriticality || node.criticalityOverride)
    : false;
  const sliderValue = criticality === ""
    ? (baselineCriticality ?? node.criticalityScore ?? 5)
    : Number(criticality);
  const hasCriticalityOverride = node.criticalityOverride ?? false;

  const changes: string[] = [];
  if (node.name !== baselineName) changes.push("Name");
  if (node.layer !== baselineLayer) changes.push("Layer");
  if ((node.shape ?? "") !== baselineShape) changes.push("Shape");
  if ((node.color ?? "") !== baselineColor) changes.push("Color");
  if (criticalityChanged) changes.push("Criticality");

  const rawJson = node.raw ? JSON.stringify(node.raw, null, 2) : null;

  const handleSave = () => {
    onSave?.(node.id, {
      name: name.trim() || undefined,
      layer: layer === "" ? null : Number(layer),
      shape: shape || null,
      color: color || null,
    });
  };

  const handleSaveCriticality = () => {
    if (criticality === "" || criticality < 1 || criticality > 10) return;
    onSaveCriticality?.(node.id, Number(criticality));
  };

  const renderStars = (score: number): string => {
    const full = Math.floor(score / 2);
    const half = score % 2 === 1;
    let stars = "★".repeat(full);
    if (half) stars += "⯪";
    stars += "☆".repeat(5 - full - (half ? 1 : 0));
    return stars;
  };

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
        X
      </button>

      <h3 style={{ marginTop: 0, marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
        Resource
        {node.aiAnnotation && (
          <span
            title="AI-suggested metadata"
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
      </h3>

      <div style={{ marginBottom: 12, fontSize: 12, color: "#9AA0A6" }}>
        ID: {node.id}
      </div>

      {node.aiAnnotation && (
        <div
          style={{
            marginBottom: 16,
            padding: 12,
            border: "1px dashed #444",
            background: "#1b1b1b",
            borderRadius: 4
          }}
          title="LLM suggestion; non-authoritative"
        >
          <div style={{ fontWeight: 600, marginBottom: 6 }}>AI suggestion</div>
          <div style={{ fontSize: 13, color: "#e5e5e5" }}>
            Name: {node.aiAnnotation.display_name ?? node.name}
          </div>
          {node.originalName && (
            <div style={{ fontSize: 12, color: "#9AA0A6", marginTop: 4 }}>
              Original: {node.originalName}
            </div>
          )}
          {node.aiAnnotation.priority && (
            <div style={{ fontSize: 12, color: "#9AA0A6", marginTop: 4 }}>
              Priority: {node.aiAnnotation.priority}
            </div>
          )}
          {node.aiAnnotation.criticality_score !== undefined && (
            <div style={{ fontSize: 12, color: "#9AA0A6", marginTop: 4 }}>
              Criticality: {node.aiAnnotation.criticality_score ?? 0}
            </div>
          )}
          {node.aiAnnotation.confidence !== undefined && (
            <div style={{ fontSize: 12, color: "#9AA0A6", marginTop: 4 }}>
              Confidence: {Math.round((node.aiAnnotation.confidence ?? 0) * 100)}%
            </div>
          )}
          {node.aiAnnotation.reason && (
            <div style={{ fontSize: 12, color: "#9AA0A6", marginTop: 6 }}>
              Reason: {node.aiAnnotation.reason}
            </div>
          )}
        </div>
      )}

      <div style={{ marginBottom: 14 }}>
        <div style={{ marginBottom: 6 }}><strong>Name</strong></div>
        <input
          value={name}
          onChange={e => setName(e.target.value)}
          placeholder="Display name"
          style={{
            width: "100%",
            padding: "8px 10px",
            background: "#1a1a1a",
            color: "#fff",
            border: "1px solid #333"
          }}
        />
      </div>

      <div style={{ marginBottom: 14 }}>
        <div style={{ marginBottom: 6 }}><strong>Layer (importance)</strong></div>
        <input
          type="number"
          value={layer}
          onChange={e => setLayer(e.target.value === "" ? "" : Number(e.target.value))}
          placeholder="1 (L0), 2 (L1), 3 (L2)"
          style={{
            width: "100%",
            padding: "8px 10px",
            background: "#1a1a1a",
            color: "#fff",
            border: "1px solid #333"
          }}
        />
      </div>

      <div style={{ marginBottom: 14 }}>
        <div style={{ marginBottom: 6 }}><strong>Shape</strong></div>
        <select
          value={shape}
          onChange={e => setShape(e.target.value)}
          style={{
            width: "100%",
            padding: "8px 10px",
            background: "#1a1a1a",
            color: "#fff",
            border: "1px solid #333"
          }}
        >
          <option value="">Default</option>
          {SHAPES.map(s => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
      </div>

      <div style={{ marginBottom: 14 }}>
        <div style={{ marginBottom: 6 }}><strong>Color</strong></div>
        <input
          type="color"
          value={color || "#177ddc"}
          onChange={e => setColor(e.target.value)}
          style={{
            width: "100%",
            padding: "6px",
            background: "#1a1a1a",
            border: "1px solid #333"
          }}
        />
      </div>

      <div style={{ marginBottom: 18 }}>
        <div style={{ marginBottom: 6, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <strong>Criticality (1-10)</strong>
          {baselineCriticality !== null && (
            <span style={{ fontSize: 11, color: "#9AA0A6" }}>AI: {baselineCriticality}/10</span>
          )}
        </div>
        <input
          type="range"
          min={1}
          max={10}
          step={1}
          value={sliderValue}
          onChange={e => setCriticality(Number(e.target.value))}
          style={{ width: "100%" }}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8 }}>
          <input
            type="number"
            min={1}
            max={10}
            value={criticality === "" ? "" : criticality}
            placeholder={baselineCriticality !== null ? String(baselineCriticality) : "AI"}
            onChange={e => {
              const v = e.target.value;
              if (v === "") return setCriticality("");
              const num = Number(v);
              if (Number.isNaN(num)) return;
              const clamped = Math.max(1, Math.min(10, num));
              setCriticality(clamped);
            }}
            style={{
              width: 80,
              padding: "6px 8px",
              background: "#1a1a1a",
              color: "#fff",
              border: "1px solid #333"
            }}
          />
          <span style={{ fontSize: 13, color: "#e5e5e5" }}>
            {effectiveCriticality !== null
              ? `${renderStars(effectiveCriticality)} (${effectiveCriticality}/10)`
              : "No override"}
          </span>
        </div>
        <div style={{ fontSize: 12, color: "#9AA0A6", marginTop: 6 }}>
          {hasCriticalityOverride ? "User override active" : "AI suggestion will be used if unset"}
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
          <button
            onClick={handleSaveCriticality}
            disabled={effectiveCriticality === null || effectiveCriticality < 1 || effectiveCriticality > 10}
            style={{
              flex: 1,
              padding: "8px 10px",
              background: "#2ea043",
              color: "#fff",
              border: "none",
              cursor: effectiveCriticality === null ? "not-allowed" : "pointer"
            }}
          >
            Save criticality
          </button>
          <button
            onClick={() => onResetCriticality?.(node.id)}
            style={{
              flex: 1,
              padding: "8px 10px",
              background: "#1f2937",
              color: "#fff",
              border: "1px solid #333",
              cursor: "pointer"
            }}
            title="Remove user-set criticality"
          >
            Reset
          </button>
        </div>
      </div>

      <button
        onClick={handleSave}
        style={{
          width: "100%",
          padding: "10px 12px",
          background: "#2ea043",
          color: "#fff",
          border: "none",
          cursor: "pointer"
        }}
      >
        Save
      </button>

      <div style={{ marginTop: 12, marginBottom: 8, fontSize: 12, color: "#9AA0A6" }}>
        Changes vs original: {changes.length ? changes.join(", ") : "None"}
      </div>

      <button
        onClick={() => onReset?.()}
        style={{
          width: "100%",
          padding: "10px 12px",
          background: "#1f2937",
          color: "#fff",
          border: "1px solid #333",
          cursor: "pointer"
        }}
      >
        Reset to defaults
      </button>

      {rawJson && (
        <details style={{ marginTop: 16 }}>
          <summary style={{ cursor: "pointer", color: "#9AA0A6" }}>
            Raw node data
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

export default NodeDrawer;
