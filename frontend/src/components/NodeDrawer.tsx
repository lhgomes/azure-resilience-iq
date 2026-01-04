import React, { useEffect, useState } from "react";
import { AZURE_ICON_MANIFEST } from "../utils/azureIconManifest";

export interface NodeData {
  id: string;
  name: string;
  type?: string;
  layer?: number;
  color?: string;
  icon?: string;
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
  aiLayerEnabled: boolean;
  userLayerEnabled: boolean;
  onClose?: () => void;
  onSave?: (nodeId: string, payload: { name?: string; layer?: number | null; color?: string | null; icon?: string | null; criticality?: number | null }) => void;
  onReset?: () => void;
}

const NodeDrawer: React.FC<Props> = ({ node, aiLayerEnabled, userLayerEnabled, onClose, onSave, onReset }) => {
  const [name, setName] = useState("");
  const [layer, setLayer] = useState<number | "">("");
  const [color, setColor] = useState<string>("");
  const [icon, setIcon] = useState<string>("");
  const [criticality, setCriticality] = useState<number | "">("");

  const UiBadge = ({ title }: { title?: string }) => (
    <span
      title={title ?? "User input"}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "1px 6px",
        borderRadius: 999,
        background: "#1e4620",
        color: "#ffffff",
        fontSize: 11,
        fontWeight: 700,
        border: "1px solid #2ea043",
        lineHeight: 1.2,
      }}
    >
      Ui
    </span>
  );

  useEffect(() => {
    if (!node) return;
    setName(node.name ?? "");
    setLayer(typeof node.layer === "number" ? node.layer : "");
    setColor(node.color ?? "");
    setIcon(node.icon ?? "");
    setCriticality(typeof node.criticalityScore === "number" ? node.criticalityScore : "");
  }, [node]);

  if (!node) return null;

  const rawMeta = (node.raw as any)?.metadata ?? {};

  const baselineName = rawMeta.original_name ?? node.originalName ?? node.name;
  const baselineLayer = rawMeta.original_importance ?? rawMeta.importance ?? node.layer;
  const baselineColor = (node.raw as any)?.metadata?.color_override ?? "";
  const baselineIcon = (node.raw as any)?.metadata?.icon_override ?? (node.raw as any)?.metadata?.icon ?? "";
  const baselineCriticality = node.aiAnnotation?.criticality_score ?? null;
  const effectiveCriticality = criticality === "" ? null : Number(criticality);
  const criticalityChanged = effectiveCriticality !== null
    ? (baselineCriticality === null ? true : effectiveCriticality !== baselineCriticality || node.criticalityOverride)
    : false;
  const sliderValue = criticality === ""
    ? (baselineCriticality ?? node.criticalityScore ?? 5)
    : Number(criticality);
  const hasCriticalityOverride = node.criticalityOverride ?? false;

  const userChanges: string[] = [];
  const aiChanges: string[] = [];

  const nameOverride = rawMeta.name_override as string | undefined;
  const layerOverride = rawMeta.layer_override as number | undefined;
  const colorOverride = rawMeta.color_override as string | undefined;
  const iconOverride = rawMeta.icon_override as string | undefined;
  const criticalityOverride = rawMeta.criticality_override as number | undefined;

  const nameUserOverridden = userLayerEnabled && (typeof nameOverride === "string" && nameOverride.length > 0);
  const layerUserOverridden = userLayerEnabled && typeof layerOverride === "number";
  const colorUserOverridden = userLayerEnabled && (typeof colorOverride === "string" && colorOverride.length > 0);
  const iconUserOverridden = userLayerEnabled && (typeof iconOverride === "string" && iconOverride.length > 0);
  const criticalityUserOverridden = userLayerEnabled && typeof criticalityOverride === "number";

  if (nameUserOverridden) userChanges.push("Name");
  if (layerUserOverridden) userChanges.push("Layer");
  if (colorUserOverridden) userChanges.push("Color");
  if (iconUserOverridden) userChanges.push("Icon");
  const aiCriticality = node.aiAnnotation?.criticality_score;
  const userCriticalityIsEffectiveChange =
    node.criticalityOverride &&
    (typeof aiCriticality !== "number" || node.criticalityScore !== aiCriticality);
  if (userCriticalityIsEffectiveChange) userChanges.push("Criticality");

  if (aiLayerEnabled && node.aiAnnotation) {
    const ann = node.aiAnnotation;
    const nameAiApplied =
      !nameUserOverridden &&
      typeof ann.display_name === "string" &&
      ann.display_name.length > 0 &&
      node.name === ann.display_name;

    const layerAiApplied =
      userLayerEnabled && !layerUserOverridden
        ? (typeof ann.layer === "number" && node.layer === ann.layer)
        : (typeof ann.layer === "number" && node.layer === ann.layer);

    const criticalityAiApplied =
      typeof ann.criticality_score === "number" &&
      node.criticalityScore === ann.criticality_score;

    if (nameAiApplied) aiChanges.push("Name");
    if (layerAiApplied) aiChanges.push("Layer");
    if (criticalityAiApplied) aiChanges.push("Criticality");
  }

  const rawJson = node.raw ? JSON.stringify(node.raw, null, 2) : null;

  const handleSave = () => {
    onSave?.(node.id, {
      name: name.trim() || undefined,
      layer: layer === "" ? null : Number(layer),
      color: color || null,
      icon: icon || null,
      criticality: effectiveCriticality,
    });
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
        {userLayerEnabled && (node.override || node.criticalityOverride) && (
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

      <div style={{ marginBottom: 14 }}>
        <div style={{ marginBottom: 6, display: "flex", alignItems: "center", gap: 8 }}>
          <strong>Name</strong>
          {nameUserOverridden && <UiBadge title="User input: Name" />}
        </div>
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
        <div style={{ marginBottom: 6, display: "flex", alignItems: "center", gap: 8 }}>
          <strong>Layer (importance)</strong>
          {layerUserOverridden && <UiBadge title="User input: Layer" />}
        </div>
        <select
          value={layer === "" ? "" : String(layer)}
          onChange={e => setLayer(e.target.value === "" ? "" : Number(e.target.value))}
          style={{
            width: "100%",
            padding: "8px 10px",
            background: "#1a1a1a",
            color: "#fff",
            border: "1px solid #333"
          }}
        >
          <option value="">Default</option>
          <option value="1">L0</option>
          <option value="2">L1</option>
          <option value="3">L2</option>
        </select>
      </div>

      <div style={{ marginBottom: 14 }}>
        <div style={{ marginBottom: 6, display: "flex", alignItems: "center", gap: 8 }}>
          <strong>Icon</strong>
          {iconUserOverridden && <UiBadge title="User input: Icon" />}
        </div>
        <select
          value={icon}
          onChange={e => setIcon(e.target.value)}
          style={{
            width: "100%",
            padding: "8px 10px",
            background: "#1a1a1a",
            color: "#fff",
            border: "1px solid #333"
          }}
        >
          <option value="">Default</option>
          {Object.entries(AZURE_ICON_MANIFEST).map(([category, files]) => (
            <optgroup key={category} label={category}>
              {files.map(file => (
                <option key={`${category}/${file}`} value={`/Icons/${category}/${file}`}>
                  {file}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>

      <div style={{ marginBottom: 14 }}>
        <div style={{ marginBottom: 6, display: "flex", alignItems: "center", gap: 8 }}>
          <strong>Color</strong>
          {colorUserOverridden && <UiBadge title="User input: Color" />}
        </div>
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
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <strong>Criticality (1-10)</strong>
            {criticalityUserOverridden && (
              <UiBadge
                title={
                  typeof aiCriticality === "number" && node.criticalityScore === aiCriticality
                    ? "User override: Criticality (same as AI)"
                    : "User input: Criticality"
                }
              />
            )}
          </div>
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

      <button
        onClick={() => onReset?.()}
        style={{
          marginTop: 10,
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

      {node.aiAnnotation && (
        <div
          style={{
            marginTop: 16,
            marginBottom: 16,
            padding: 12,
            border: "1px dashed #444",
            background: "#1b1b1b",
            borderRadius: 4
          }}
          title="LLM suggestion; non-authoritative"
        >
          <div style={{ fontWeight: 600, marginBottom: 6 }}>AI suggestion</div>
          <div style={{ fontSize: 13, color: "#9AA0A6" }}>
            Name: <span style={{ color: "#e5e5e5" }}>{node.aiAnnotation.display_name ?? node.name}</span>
          </div>
          {node.aiAnnotation.priority && (
            <div style={{ fontSize: 13, color: "#9AA0A6", marginTop: 4 }}>
              Priority: <span style={{ color: "#e5e5e5" }}>{node.aiAnnotation.priority}</span>
            </div>
          )}
          {node.aiAnnotation.criticality_score !== undefined && (
            <div style={{ fontSize: 13, color: "#9AA0A6", marginTop: 4 }}>
              Criticality: <span style={{ color: "#e5e5e5" }}>{node.aiAnnotation.criticality_score ?? 0}</span>
            </div>
          )}
          {node.aiAnnotation.confidence !== undefined && (
            <div style={{ fontSize: 13, color: "#9AA0A6", marginTop: 4 }}>
              Confidence: <span style={{ color: "#e5e5e5" }}>{Math.round((node.aiAnnotation.confidence ?? 0) * 100)}%</span>
            </div>
          )}
          {node.aiAnnotation.reason && (
            <div style={{ fontSize: 13, color: "#9AA0A6", marginTop: 6 }}>
              Reason: <span style={{ color: "#e5e5e5" }}>{node.aiAnnotation.reason}</span>
            </div>
          )}
        </div>
      )}

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
