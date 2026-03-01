import React, { useEffect, useMemo, useState } from "react";
import { AZURE_ICON_MANIFEST } from "../utils/azureIconManifest";
import { renderStars } from "../domain/graphView";
import { CloseIconButton } from "./common/buttons";

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
  showShowInGraph?: boolean;
  onShowInGraph?: () => void;
  onClose?: () => void;
  onSave?: (nodeId: string, payload: { name?: string; layer?: number | null; color?: string | null; icon?: string | null; criticality?: number | null }) => void;
  onReset?: () => void;
}

type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

const JsonLeaf: React.FC<{ value: JsonValue }> = ({ value }) => {
  if (value === null) return <span style={{ color: "#9ca3af" }}>null</span>;
  if (typeof value === "string") return <span style={{ color: "#0f766e" }}>{`"${value}"`}</span>;
  if (typeof value === "number") return <span style={{ color: "#1d4ed8" }}>{value}</span>;
  if (typeof value === "boolean") return <span style={{ color: "#7c3aed" }}>{String(value)}</span>;
  return <span style={{ color: "#6b7280" }}>—</span>;
};

const JsonNode: React.FC<{ label?: string; value: JsonValue; depth?: number }> = ({ label, value, depth = 0 }) => {
  const indent = depth * 14;

  if (value === null || typeof value !== "object") {
    return (
      <div style={{ marginLeft: indent, padding: "2px 0" }}>
        {label && <span style={{ color: "#6b7280", marginRight: 6 }}>{label}:</span>}
        <JsonLeaf value={value} />
      </div>
    );
  }

  if (Array.isArray(value)) {
    return (
      <details open={depth < 1} style={{ marginLeft: indent, padding: "2px 0" }}>
        <summary style={{ cursor: "pointer", color: "#374151", fontWeight: 500 }}>
          {label ? `${label}: ` : ""}[{value.length}]
        </summary>
        <div style={{ marginTop: 4 }}>
          {value.length === 0 ? (
            <div style={{ marginLeft: 14, color: "#9ca3af" }}>[]</div>
          ) : (
            value.map((item, index) => (
              <JsonNode key={`${label || "array"}-${index}`} label={`[${index}]`} value={item as JsonValue} depth={depth + 1} />
            ))
          )}
        </div>
      </details>
    );
  }

  const entries = Object.entries(value as Record<string, JsonValue>);
  return (
    <details open={depth < 1} style={{ marginLeft: indent, padding: "2px 0" }}>
      <summary style={{ cursor: "pointer", color: "#374151", fontWeight: 500 }}>
        {label ? `${label}: ` : ""}{`{${entries.length}}`}
      </summary>
      <div style={{ marginTop: 4 }}>
        {entries.length === 0 ? (
          <div style={{ marginLeft: 14, color: "#9ca3af" }}>{"{}"}</div>
        ) : (
          entries.map(([key, child]) => (
            <JsonNode key={`${label || "object"}-${key}`} label={key} value={child} depth={depth + 1} />
          ))
        )}
      </div>
    </details>
  );
};

const NodeDrawer: React.FC<Props> = ({ node, aiLayerEnabled, userLayerEnabled, showShowInGraph = false, onShowInGraph, onClose, onSave, onReset }) => {
  const [name, setName] = useState("");
  const [layer, setLayer] = useState<number | "">("");
  const [color, setColor] = useState<string>("");
  const [icon, setIcon] = useState<string>("");
  const [iconSearch, setIconSearch] = useState<string>("");
  const [showIconDropdown, setShowIconDropdown] = useState<boolean>(false);
  const [criticality, setCriticality] = useState<number | "">("");
  const [activeTab, setActiveTab] = useState<"essentials" | "configuration" | "ai" | "raw">("essentials");

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
    setActiveTab("essentials");
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

  const rawData = useMemo<JsonValue | null>(() => {
    if (!node.raw) return null;
    try {
      return JSON.parse(JSON.stringify(node.raw)) as JsonValue;
    } catch {
      return null;
    }
  }, [node.raw]);

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

  const extractFromResourceId = (segment: string): string | null => {
    const parts = resourceId.split("/").filter(Boolean);
    const idx = parts.findIndex((part) => part.toLowerCase() === segment.toLowerCase());
    if (idx === -1 || idx + 1 >= parts.length) return null;
    return parts[idx + 1] || null;
  };

  const resilienceMeta = ((rawMeta as any).resilience ?? ((node.raw as any)?.resilience ?? {})) as any;
  const resilienceScoreRaw = resilienceMeta?.resilience_score ?? resilienceMeta?.score;
  const resilienceScore = typeof resilienceScoreRaw === "number"
    ? `${(resilienceScoreRaw <= 1 ? resilienceScoreRaw * 100 : resilienceScoreRaw).toFixed(1)}%`
    : "—";
  const zoneArray =
    Array.isArray(rawMeta.zones) ? rawMeta.zones
      : Array.isArray((node.raw as any)?.zones) ? (node.raw as any).zones
      : null;
  const resolvedZone =
    (rawMeta.zone as string | number | undefined) ??
    (rawMeta.availability_zone as string | number | undefined) ??
    (rawMeta.availabilityZone as string | number | undefined) ??
    ((node.raw as any)?.zone as string | number | undefined) ??
    ((node.raw as any)?.availability_zone as string | number | undefined) ??
    ((node.raw as any)?.availabilityZone as string | number | undefined) ??
    (zoneArray && zoneArray.length > 0 ? zoneArray.join(", ") : undefined);
  const checksFromArray = Array.isArray(resilienceMeta?.checks) ? resilienceMeta.checks : [];
  const passedChecks = typeof resilienceMeta?.passed_checks === "number"
    ? resilienceMeta.passed_checks
    : checksFromArray.filter((check: any) => String(check?.status || "").toLowerCase() === "pass").length;
  const failedChecks = typeof resilienceMeta?.failed_checks === "number"
    ? resilienceMeta.failed_checks
    : checksFromArray.filter((check: any) => String(check?.status || "").toLowerCase() === "fail").length;
  const totalChecks = typeof resilienceMeta?.total_checks === "number"
    ? resilienceMeta.total_checks
    : checksFromArray.length;
  const checksSummary = totalChecks > 0
    ? `${passedChecks} passed / ${failedChecks} failed`
    : "—";
  const viewLevelValue = layer === "" ? (typeof node.layer === "number" ? node.layer : 2) : Number(layer);

  const essentialItems: Array<{ label: string; value: string }> = [
    { label: "Resource ID", value: resourceId },
    { label: "Resource group", value: extractFromResourceId("resourceGroups") || "—" },
    { label: "Subscription ID", value: extractFromResourceId("subscriptions") || "—" },
    { label: "Type", value: node.type || "—" },
    { label: "View Level", value: typeof node.layer === "number" ? `L${node.layer}` : "—" },
    {
      label: "Location",
      value:
        ((rawMeta.location as string | undefined) ??
          ((node.raw as any)?.location as string | undefined) ??
          "—"),
    },
    {
      label: "Availability zone",
      value: resolvedZone !== undefined && resolvedZone !== null && String(resolvedZone).trim() !== ""
        ? String(resolvedZone)
        : "—",
    },
    { label: "Resilience score", value: resilienceScore },
    { label: "Checks", value: checksSummary },
  ];

  return (
    <div
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        background: "rgba(15, 23, 42, 0.35)",
        backdropFilter: "blur(2px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 10000,
        padding: "20px",
      }}
      onClick={onClose}
    >
      <div
        style={{
          width: "min(920px, 94vw)",
          height: "55vh",
          background: "#ffffff",
          color: "#111827",
          border: "1px solid #d1d5db",
          borderRadius: "12px",
          padding: 20,
          boxSizing: "border-box",
          position: "relative",
          overflowY: "auto",
          boxShadow: "0 24px 48px -20px rgba(15, 23, 42, 0.35)",
        }}
        onClick={(event) => event.stopPropagation()}
      >
        <CloseIconButton
          onClick={onClose}
          title="Close"
          absolute
          top={10}
          right={10}
        />

        <div style={{ marginBottom: 16, paddingRight: 40, display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
          <div style={{ minWidth: 0 }}>
            <h2 style={{ marginTop: 0, marginBottom: 6, fontSize: 22, fontWeight: 700, color: "#111827" }}>
              {name || node.name || "Resource"}
            </h2>
            <div style={{ marginBottom: 0, display: "flex", alignItems: "center", gap: 8 }}>
              {node.aiAnnotation && (
                <span
                  title="AI-suggested metadata"
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    padding: "2px 8px",
                    borderRadius: 999,
                    background: "#fef3c7",
                    color: "#92400e",
                    fontSize: 11,
                    fontWeight: 700,
                    border: "1px solid #fcd34d"
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
                    padding: "2px 8px",
                    borderRadius: 999,
                    background: "#dcfce7",
                    color: "#166534",
                    fontSize: 11,
                    fontWeight: 700,
                    border: "1px solid #86efac"
                  }}
                >
                  Ui
                </span>
              )}
            </div>
          </div>

          {(portalUrl || (showShowInGraph && onShowInGraph)) && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end", marginTop: 6 }}>
              {portalUrl && (
                <a
                  href={portalUrl}
                  target="_blank"
                  rel="noreferrer"
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "8px 12px",
                    background: "#0078d4",
                    color: "#fff",
                    border: "1px solid #0078d4",
                    borderRadius: 6,
                    textDecoration: "none",
                    fontWeight: 600,
                    fontSize: 13,
                  }}
                >
                  Open in Azure portal
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                    <polyline points="15 3 21 3 21 9" />
                    <line x1="10" y1="14" x2="21" y2="3" />
                  </svg>
                </a>
              )}
              {showShowInGraph && onShowInGraph && (
                <button
                  onClick={() => {
                    onShowInGraph();
                    onClose?.();
                  }}
                  type="button"
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "8px 12px",
                    background: "#ffffff",
                    color: "#5b21b6",
                    border: "1px solid #c4b5fd",
                    borderRadius: 6,
                    fontWeight: 600,
                    fontSize: 13,
                    cursor: "pointer"
                  }}
                >
                  Show in Graph
                </button>
              )}
            </div>
          )}
        </div>

        <div
          style={{
            marginBottom: 16,
            display: "flex",
            gap: 28,
            flexWrap: "wrap",
            borderBottom: "1px solid #e5e7eb",
          }}
        >
          {[
            { key: "essentials", label: "Essentials" },
            { key: "configuration", label: "Configuration" },
            { key: "ai", label: "AI Annotations" },
            { key: "raw", label: "Raw Data" },
          ].map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => setActiveTab(tab.key as "essentials" | "configuration" | "ai" | "raw")}
              style={{
                padding: "10px 2px 12px",
                marginBottom: -1,
                borderRadius: 0,
                border: "none",
                borderBottom: activeTab === tab.key ? "3px solid #2563eb" : "3px solid transparent",
                background: "transparent",
                color: activeTab === tab.key ? "#111827" : "#374151",
                fontWeight: activeTab === tab.key ? 600 : 500,
                fontSize: 14,
                cursor: "pointer",
                width: "calc(1em + 12%)",
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {activeTab === "essentials" && (
          <div
            style={{
              border: "1px solid #e5e7eb",
              borderRadius: 10,
              background: "#ffffff",
              padding: 14,
              marginBottom: 14,
            }}
          >
            <div style={{ fontSize: 14, fontWeight: 700, color: "#111827", marginBottom: 10 }}>Essentials</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px 24px" }}>
              {essentialItems.map((item) => (
                <div
                  key={item.label}
                  style={{
                    minWidth: 0,
                    gridColumn: item.label === "Resource ID" ? "1 / -1" : undefined,
                  }}
                >
                  <div style={{ fontSize: 12, color: "#6b7280", marginBottom: 2 }}>{item.label}</div>
                  <div
                    style={{
                      fontSize: 13,
                      color: "#111827",
                      fontWeight: 500
                    }}
                    title={item.label === "Resource ID" ? item.value : undefined}
                  >
                    {item.value}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {activeTab === "configuration" && (
          <div
            style={{
              border: "1px solid #e5e7eb",
              borderRadius: 10,
              background: "#ffffff",
              padding: 14,
            }}
          >
            <div style={{ fontSize: 14, fontWeight: 700, color: "#111827", marginBottom: 12 }}>Configuration</div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div style={{ marginBottom: 4 }}>
              <div style={{ marginBottom: 6, display: "flex", alignItems: "center", gap: 8 }}>
                <strong>Name</strong>
                {nameUserOverridden && <UiBadge title="User input: Name" />}
              </div>
              <input
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="Display name"
                style={{
                  width: "calc(100% - 20px)",
                  padding: "8px 10px",
                  background: "#ffffff",
                  color: "#111827",
                  border: "1px solid #d1d5db",
                  borderRadius: 6,
                }}
              />
            </div>

            <div style={{ marginBottom: 4 }}>
              <div style={{ marginBottom: 6, display: "flex", alignItems: "center", gap: 8 }}>
                <strong>View Level</strong>
                {layerUserOverridden && <UiBadge title="User input: Layer" />}
              </div>
              <input
                type="range"
                min={1}
                max={3}
                step={1}
                value={viewLevelValue}
                onChange={(e) => setLayer(Number(e.target.value))}
                style={{ width: "100%" }}
              />
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4, fontSize: 12, color: "#6b7280" }}>
                <span>L1</span>
                <span style={{ fontWeight: 600, color: "#111827" }}>{`L${viewLevelValue}`}</span>
                <span>L3</span>
              </div>
            </div>

            <div style={{ marginBottom: 4 }}>
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
                    width: "calc(100% - 20px)",
                    padding: "8px 10px",
                    background: "#ffffff",
                    color: "#111827",
                    border: "1px solid #d1d5db",
                    borderRadius: 6,
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
                      background: "#ffffff",
                      border: "1px solid #d1d5db",
                      borderRadius: 6,
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
                        borderBottom: "1px solid #e5e7eb",
                        color: "#6b7280",
                      }}
                      onMouseEnter={e => e.currentTarget.style.background = "#f3f4f6"}
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
                          borderBottom: "1px solid #f3f4f6",
                        }}
                        onMouseEnter={e => e.currentTarget.style.background = "#f9fafb"}
                        onMouseLeave={e => e.currentTarget.style.background = "transparent"}
                      >
                        <div style={{ fontSize: 11, color: "#6b7280", marginBottom: 2 }}>
                          {opt.category}
                        </div>
                        <div style={{ color: "#111827" }}>
                          {opt.file}
                        </div>
                      </div>
                    ))}
                    {filteredIconOptions.length === 0 && (
                      <div style={{ padding: "12px", color: "#6b7280", textAlign: "center" }}>
                        No icons found
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>

            <div style={{ marginBottom: 4 }}>
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
                  background: "#ffffff",
                  border: "1px solid #d1d5db",
                  borderRadius: 6,
                }}
              />
            </div>
            </div>

            <div style={{ marginTop: 8, marginBottom: 18 }}>
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
                <span style={{ fontSize: 11, color: "#6b7280" }}>AI: {baselineCriticality}/10</span>
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
                  background: "#ffffff",
                  color: "#111827",
                  border: "1px solid #d1d5db",
                  borderRadius: 6,
                }}
              />
              <span style={{ fontSize: 13, color: "#111827" }}>
                {effectiveCriticality !== null
                  ? `${renderStars(effectiveCriticality)} (${effectiveCriticality}/10)`
                  : "No override"}
              </span>
            </div>
            <div style={{ fontSize: 12, color: "#6b7280", marginTop: 6, display: "none" }}>
              {criticalityUserOverridden ? "User override active" : "AI suggestion will be used if unset"}
            </div>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
            <button
              onClick={() => onReset?.()}
              disabled={!hasAnyOverride}
              style={{
                padding: "10px 12px",
                background: "#ffffff",
                color: hasAnyOverride ? "#374151" : "#9ca3af",
                border: "1px solid #d1d5db",
                borderRadius: 6,
                cursor: hasAnyOverride ? "pointer" : "not-allowed",
                opacity: hasAnyOverride ? 1 : 0.6
              }}
            >
              Reset to defaults
            </button>
            <button
              onClick={handleSave}
              disabled={!hasChanges}
              style={{
                padding: "10px 16px",
                background: hasChanges ? "#0078d4" : "#93c5fd",
                color: "#fff",
                border: "1px solid #0078d4",
                borderRadius: 6,
                cursor: hasChanges ? "pointer" : "not-allowed",
                opacity: hasChanges ? 1 : 0.7
              }}
            >
              Save
            </button>
            </div>
          </div>
        )}

        {activeTab === "ai" && (
          node.aiAnnotation ? (
            <div
              style={{
                marginTop: 4,
                marginBottom: 16,
                padding: 12,
                border: "1px solid #fde68a",
                background: "#fffbeb",
                borderRadius: 8
              }}
              title="LLM suggestion; non-authoritative"
            >
              <div style={{ fontWeight: 600, marginBottom: 6, color: "#92400e" }}>AI suggestion</div>
              <div style={{ fontSize: 13, color: "#92400e" }}>
                Name: <span style={{ color: "#111827" }}>{node.aiAnnotation.display_name ?? node.name}</span>
              </div>
              {node.aiAnnotation.priority && (
                <div style={{ fontSize: 13, color: "#92400e", marginTop: 4 }}>
                  Priority: <span style={{ color: "#111827" }}>{node.aiAnnotation.priority}</span>
                </div>
              )}
              {node.aiAnnotation.layer !== undefined && (
                <div style={{ fontSize: 13, color: "#92400e", marginTop: 4 }}>
                  View Level: <span style={{ color: "#111827" }}>{`L${node.aiAnnotation.layer}`}</span>
                </div>
              )}
              {node.aiAnnotation.criticality_score !== undefined && (
                <div style={{ fontSize: 13, color: "#92400e", marginTop: 4 }}>
                  Criticality: <span style={{ color: "#111827" }}>{node.aiAnnotation.criticality_score ?? 0}</span>
                </div>
              )}
              {node.aiAnnotation.criticality_weight !== undefined && (
                <div style={{ fontSize: 13, color: "#92400e", marginTop: 4 }}>
                  Weight: <span style={{ color: "#111827" }}>{node.aiAnnotation.criticality_weight.toFixed(2)}%</span>
                </div>
              )}
              {node.aiAnnotation.confidence !== undefined && (
                <div style={{ fontSize: 13, color: "#92400e", marginTop: 4 }}>
                  Confidence: <span style={{ color: "#111827" }}>{Math.round((node.aiAnnotation.confidence ?? 0) * 100)}%</span>
                </div>
              )}
              {node.aiAnnotation.reason && (
                <div style={{ fontSize: 13, color: "#92400e", marginTop: 6 }}>
                  Reason: <span style={{ color: "#111827" }}>{node.aiAnnotation.reason}</span>
                </div>
              )}
            </div>
          ) : (
            <div style={{ border: "1px solid #e5e7eb", borderRadius: 8, padding: 12, color: "#6b7280" }}>
              No AI annotations available for this resource.
            </div>
          )
        )}

        {activeTab === "raw" && (
          rawData ? (
            <div
              style={{
                marginTop: 4,
                background: "#f9fafb",
                padding: 12,
                border: "1px solid #e5e7eb",
                borderRadius: 8,
                color: "#1f2937",
                overflowX: "auto",
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, Courier New, monospace",
                fontSize: 12,
              }}
            >
              <JsonNode value={rawData} />
            </div>
          ) : (
            <div style={{ border: "1px solid #e5e7eb", borderRadius: 8, padding: 12, color: "#6b7280" }}>
              Raw node data is not available.
            </div>
          )
        )}
      </div>
    </div>
  );
};

export default NodeDrawer;
