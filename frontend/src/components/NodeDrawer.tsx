import React, { useEffect, useState } from "react";
import { AZURE_ICON_MANIFEST } from "../utils/azureIconManifest";
import { renderStars } from "../domain/graphView";

export interface NodeData {
  id: string;
  name: string;
  type?: string;
  layer?: number;
  color?: string;
  icon?: string;
  criticalityScore?: number;
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
  criticality_weight?: number;
  hide_by_default?: boolean;
  confidence?: number;
  reason?: string;
  source?: string;
  icon?: string;
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
  const [iconSearch, setIconSearch] = useState<string>("");
  const [showIconDropdown, setShowIconDropdown] = useState<boolean>(false);
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
    const rawMeta = (node.raw as any)?.metadata ?? {};
    const userOverride = (rawMeta.user_override as Record<string, unknown> | undefined) ?? {};

    // Priority: 1. User override, 2. AI suggestion, 3. Original data
    const aiName = node.aiAnnotation?.display_name;
    const originalName = node.originalName ?? node.name;
    const effectiveName = (userOverride as any).name ?? aiName ?? originalName ?? "";

    const aiLayer = node.aiAnnotation?.layer;
    const originalLayer = rawMeta.importance ?? node.layer;
    const effectiveLayer = (userOverride as any).layer ?? aiLayer ?? originalLayer;

    const aiIcon = node.aiAnnotation?.icon;
    const originalIcon = rawMeta.icon;
    const effectiveIcon = (userOverride as any).icon ?? aiIcon ?? originalIcon ?? "";

    const aiCriticality = node.aiAnnotation?.criticality_score;
    const userCriticality = (userOverride as any).criticality_score as number | undefined;
    const effectiveCriticalityScore = userCriticality ?? aiCriticality ?? node.criticalityScore;

    const userColor = (userOverride as any).color as string | undefined;
    const baseColor = rawMeta.color as string | undefined;

    setName(effectiveName);
    setLayer(typeof effectiveLayer === "number" ? effectiveLayer : "");
    setColor(userColor ?? baseColor ?? "");
    setIcon(effectiveIcon);
    setCriticality(typeof effectiveCriticalityScore === "number" ? effectiveCriticalityScore : "");
  }, [node]);

  if (!node) return null;

  const rawMeta = (node.raw as any)?.metadata ?? {};
  const isVirtual = Boolean(rawMeta?.virtual);
  const resourceId = typeof (node.raw as any)?.id === "string" ? ((node.raw as any).id as string) : node.id;
  const tenantId =
    (rawMeta.tenant_id as string | undefined) ??
    (rawMeta.tenantId as string | undefined) ??
    (rawMeta.tenant as string | undefined);
  const portalUrl =
    !isVirtual && typeof resourceId === "string" && resourceId.toLowerCase().startsWith("/subscriptions/")
      ? `https://portal.azure.com/#${tenantId ? `@${tenantId}/` : ""}resource${resourceId}/overview`
      : null;

  const userOverride = (rawMeta.user_override as Record<string, unknown> | undefined) ?? {};

  const baselineCriticality = node.aiAnnotation?.criticality_score ?? null;
  const effectiveCriticality = criticality === "" ? null : Number(criticality);
  const sliderValue = criticality === ""
    ? (baselineCriticality ?? node.criticalityScore ?? 5)
    : Number(criticality);

  const userChanges: string[] = [];
  const aiChanges: string[] = [];

  const nameOverride = (userOverride as any).name as string | undefined;
  const layerOverride = (userOverride as any).layer as number | undefined;
  const colorOverride = (userOverride as any).color as string | undefined;
  const iconOverride = (userOverride as any).icon as string | undefined;
  const criticalityOverride = (userOverride as any).criticality_score as number | undefined;

  const nameUserOverridden = userLayerEnabled && typeof nameOverride === "string" && nameOverride.length > 0;
  const layerUserOverridden = userLayerEnabled && typeof layerOverride === "number";
  const colorUserOverridden = userLayerEnabled && typeof colorOverride === "string" && colorOverride.length > 0;
  const iconUserOverridden = userLayerEnabled && typeof iconOverride === "string" && iconOverride.length > 0;
  const criticalityUserOverridden = userLayerEnabled && typeof criticalityOverride === "number";

  if (nameUserOverridden) userChanges.push("Name");
  if (layerUserOverridden) userChanges.push("Layer");
  if (colorUserOverridden) userChanges.push("Color");
  if (iconUserOverridden) userChanges.push("Icon");
  const aiCriticality = node.aiAnnotation?.criticality_score;
  const userCriticalityIsEffectiveChange =
    criticalityUserOverridden &&
    (typeof aiCriticality !== "number" || node.criticalityScore !== aiCriticality);
  if (userCriticalityIsEffectiveChange) userChanges.push("Criticality");

  // Check if there are any user overrides to enable the reset button
  const hasAnyOverride = Object.keys(userOverride).length > 0;

  // Build filtered icon options
  const allIconOptions: { category: string; file: string; path: string }[] = [];
  Object.entries(AZURE_ICON_MANIFEST).forEach(([category, files]) => {
    files.forEach(file => {
      allIconOptions.push({
        category,
        file,
        path: `/Icons/${category}/${file}`
      });
    });
  });

  const filteredIconOptions = iconSearch
    ? allIconOptions.filter(opt =>
        opt.file.toLowerCase().includes(iconSearch.toLowerCase()) ||
        opt.category.toLowerCase().includes(iconSearch.toLowerCase())
      )
    : allIconOptions;

  const selectedIconOption = allIconOptions.find(opt => opt.path === icon);
  const iconDisplayValue = selectedIconOption ? selectedIconOption.file : "";

  if (aiLayerEnabled && node.aiAnnotation) {
    const ann = node.aiAnnotation;
    const nameAiApplied =
      !nameUserOverridden &&
      typeof ann.display_name === "string" &&
      ann.display_name.length > 0 &&
      node.name === ann.display_name;

    const layerAiApplied =
      !layerUserOverridden &&
      typeof ann.layer === "number" &&
      node.layer === ann.layer;

    const criticalityAiApplied =
      typeof ann.criticality_score === "number" &&
      node.criticalityScore === ann.criticality_score;

    if (nameAiApplied) aiChanges.push("Name");
    if (layerAiApplied) aiChanges.push("Layer");
    if (criticalityAiApplied) aiChanges.push("Criticality");
  }

  const rawJson = node.raw ? JSON.stringify(node.raw, null, 2) : null;

  // Detect if user has made changes from the current effective values
  const hasChanges = (() => {
    const currentEffectiveName = node.name ?? "";
    const currentEffectiveLayer = node.layer ?? null;
    const currentEffectiveColor = node.color ?? "";
    const currentEffectiveIcon = node.icon ?? "";
    const currentEffectiveCriticality = node.criticalityScore ?? null;

    const nameChanged = name.trim() !== currentEffectiveName;
    const layerChanged = (layer === "" ? null : Number(layer)) !== (currentEffectiveLayer ?? null);
    const colorChanged = (color || null) !== (currentEffectiveColor || null);
    const iconChanged = (icon || null) !== (currentEffectiveIcon || null);
    const criticalityChanged = effectiveCriticality !== (currentEffectiveCriticality ?? null);

    return nameChanged || layerChanged || colorChanged || iconChanged || criticalityChanged;
  })();

  const handleSave = () => {
    onSave?.(node.id, {
      name: name.trim() || undefined,
      layer: layer === "" ? null : Number(layer),
      color: color || null,
      icon: icon || null,
      criticality: effectiveCriticality,
    });
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
        {userLayerEnabled && (node.override || criticalityUserOverridden) && (
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

      {portalUrl && (
        <div style={{ marginBottom: 12 }}>
          <a
            href={portalUrl}
            target="_blank"
            rel="noreferrer"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
              padding: "8px 10px",
              background: "#1f2937",
              color: "#93c5fd",
              border: "1px solid #374151",
              borderRadius: 6,
              textDecoration: "none",
              fontWeight: 600
            }}
          >
            Open in Azure portal
          </a>
          <div style={{ marginTop: 6, fontSize: 12, color: "#9AA0A6", wordBreak: "break-all" }}>
            {resourceId}
          </div>
        </div>
      )}

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
          <option value="1">L1</option>
          <option value="2">L2</option>
          <option value="3">L3</option>
        </select>
      </div>

      <div style={{ marginBottom: 14 }}>
        <div style={{ marginBottom: 6, display: "flex", alignItems: "center", gap: 8 }}>
          <strong>Icon</strong>
          {iconUserOverridden && <UiBadge title="User input: Icon" />}
        </div>
        <div style={{ position: "relative" }}>
          <input
            type="text"
            value={iconSearch || iconDisplayValue}
            onChange={e => {
              setIconSearch(e.target.value);
              setShowIconDropdown(true);
            }}
            onFocus={() => {
              setIconSearch("");
              setShowIconDropdown(true);
            }}
            onBlur={() => {
              setTimeout(() => setShowIconDropdown(false), 200);
            }}
            placeholder="Search or select icon..."
            style={{
              width: "100%",
              padding: "8px 10px",
              background: "#1a1a1a",
              color: "#fff",
              border: "1px solid #333",
              borderRadius: 4,
            }}
          />
          {showIconDropdown && (
            <div
              style={{
                position: "absolute",
                top: "100%",
                left: 0,
                right: 0,
                maxHeight: 300,
                overflowY: "auto",
                background: "#1a1a1a",
                border: "1px solid #333",
                borderRadius: 4,
                marginTop: 4,
                zIndex: 1000,
              }}
            >
              <div
                onClick={() => {
                  setIcon("");
                  setIconSearch("");
                  setShowIconDropdown(false);
                }}
                style={{
                  padding: "8px 12px",
                  cursor: "pointer",
                  borderBottom: "1px solid #333",
                  color: "#999",
                }}
                onMouseEnter={e => e.currentTarget.style.background = "#2a2a2a"}
                onMouseLeave={e => e.currentTarget.style.background = "transparent"}
              >
                Default
              </div>
              {filteredIconOptions.map(opt => (
                <div
                  key={opt.path}
                  onClick={() => {
                    setIcon(opt.path);
                    setIconSearch("");
                    setShowIconDropdown(false);
                  }}
                  style={{
                    padding: "8px 12px",
                    cursor: "pointer",
                    borderBottom: "1px solid #2a2a2a",
                  }}
                  onMouseEnter={e => e.currentTarget.style.background = "#2a2a2a"}
                  onMouseLeave={e => e.currentTarget.style.background = "transparent"}
                >
                  <div style={{ fontSize: 11, color: "#888", marginBottom: 2 }}>
                    {opt.category}
                  </div>
                  <div style={{ color: "#fff" }}>
                    {opt.file}
                  </div>
                </div>
              ))}
              {filteredIconOptions.length === 0 && (
                <div style={{ padding: "12px", color: "#666", textAlign: "center" }}>
                  No icons found
                </div>
              )}
            </div>
          )}
        </div>
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
          {criticalityUserOverridden ? "User override active" : "AI suggestion will be used if unset"}
        </div>
      </div>

      <button
        onClick={handleSave}
        disabled={!hasChanges}
        style={{
          width: "100%",
          padding: "10px 12px",
          background: hasChanges ? "#2ea043" : "#0f1419",
          color: hasChanges ? "#fff" : "#666",
          border: "none",
          cursor: hasChanges ? "pointer" : "not-allowed",
          opacity: hasChanges ? 1 : 0.5
        }}
      >
        Save
      </button>

      <button
        onClick={() => onReset?.()}
        disabled={!hasAnyOverride}
        style={{
          marginTop: 10,
          width: "100%",
          padding: "10px 12px",
          background: hasAnyOverride ? "#1f2937" : "#0f1419",
          color: hasAnyOverride ? "#fff" : "#666",
          border: "1px solid #333",
          cursor: hasAnyOverride ? "pointer" : "not-allowed",
          opacity: hasAnyOverride ? 1 : 0.5
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
          {node.aiAnnotation.criticality_weight !== undefined && (
            <div style={{ fontSize: 13, color: "#9AA0A6", marginTop: 4 }}>
              Weight: <span style={{ color: "#e5e5e5" }}>{node.aiAnnotation.criticality_weight.toFixed(2)}%</span>
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
