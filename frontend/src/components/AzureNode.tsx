import React, { useState, useRef, useEffect } from "react";
import { createPortal } from "react-dom";
import { Handle, Position } from "reactflow";
import { getAzureIcon } from "../utils/azureIcons";
import type { AiTooltip } from "../domain/graphView";
import ResilienceCircle from "./ResilienceCircle";

// Weight icon component - uses Power icon to represent element importance
const WeightIcon: React.FC<{ size?: number }> = ({ size = 14 }) => (
  <img 
    src="/Icons/general/10824-icon-service-Power.svg" 
    alt="weight" 
    style={{ width: `${size}px`, height: `${size}px` }}
  />
);

// Confidence icon component
const ConfidenceIcon: React.FC<{ size?: number }> = ({ size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="12" cy="12" r="10" stroke="#6b7280" strokeWidth="1.5" />
    <circle cx="12" cy="12" r="3" fill="#6b7280" />
  </svg>
);

interface AzureNodeProps {
  data: {
    label: string;
    icon?: string;
    type?: string;
    criticality_stars?: string;
    criticality_score?: number;
    confidence?: number;
    element_weight?: number;
    color?: string;
    azure_service_category?: string;
    azure_service_name?: string;
    ai_annotation?: boolean;
    user_customized?: boolean;
    ai_tooltip?: AiTooltip;
    user_tooltip?: AiTooltip;
    groupId?: string;
    onRemoveFromGroup?: () => void;
    onRemoveFromGraph?: () => void;
    metadata?: Record<string, unknown>;
  };
  isConnectable: boolean;
  selected: boolean;
}

const AzureNode: React.FC<AzureNodeProps> = ({ data, isConnectable, selected }) => {
  const [iconError, setIconError] = useState(false);
  const [showWeightTooltip, setShowWeightTooltip] = useState(false);
  const [weightTooltipPosition, setWeightTooltipPosition] = useState({ x: 0, y: 0 });
  const [resilience, setResilience] = useState<{ score: number } | null>(null);
  const nodeRef = useRef<HTMLDivElement>(null);
  const weightRef = useRef<HTMLDivElement>(null);
  const isVirtual = Boolean((data.metadata as any)?.virtual);

  useEffect(() => {
    if (showWeightTooltip && weightRef.current) {
      const rect = weightRef.current.getBoundingClientRect();
      setWeightTooltipPosition({
        x: rect.right + 8,
        y: rect.top + rect.height / 2
      });
    }
  }, [showWeightTooltip]);

  // Extract resilience score from node metadata
  useEffect(() => {
    if (data.metadata && typeof data.metadata === 'object') {
      const resilience = (data.metadata as any).resilience;
      if (resilience && typeof resilience === 'object') {
        const score = (resilience as any).resilience_score;
        if (score !== undefined && score !== null) {
          setResilience({ score });
        } else {
          setResilience(null);
        }
      } else {
        setResilience(null);
      }
    } else {
      setResilience(null);
    }
  }, [data.metadata]);

  const getNodeColor = (): string => {
    if (data.color) return data.color;
    
    switch (data.type) {
      case "vm":
      case "aks":
        return "#0078d4"; // Azure Blue
      case "vnet":
        return "#50e6ff"; // Cyan
      case "subnet":
        return "#b3e0ff"; // Light Cyan
      case "storage":
      case "sql":
      case "keyvault":
        return "#00bcf2"; // Light Blue
      case "nsg":
      case "network":
      case "nic":
      case "pip":
        return "#5EA0EF"; // Network Blue
      case "private_endpoint":
        return "#7c7c7c"; // Gray
      default:
        return "#ffffff"; // Default white
    }
  };

  const nodeColor = getNodeColor();
  const iconUrl = data.icon || getAzureIcon(data.type || "resource");

  // Category color based on resilience checks availability
  // - No checks (total_checks = 0): Light Gray (no resilience data)
  // - Has checks: Color based on criticality (customer has actions to take)
  const getCategoryColor = () => {
    // Check if resilience score exists
    const hasResilienceScore = data.metadata && 
      (data.metadata as any).resilience?.resilience_score !== undefined;
    
    // No resilience checks available - show gray
    if (!hasResilienceScore) {
      return "#b7b8baff"; // Light Gray - no resilience data
    }
    
    // Has resilience checks - use criticality-based colors
    const score = data.criticality_score ?? 5; // Default to medium if not available
    
    if (score <= 2) {
      return "#22c55e"; // Low criticality - Green
    } else if (score <= 4) {
      return "#84cc16"; // Low-Medium criticality - Lime
    } else if (score <= 6) {
      return "#eab308"; // Medium criticality - Yellow
    } else if (score <= 8) {
      return "#f97316"; // Medium-High criticality - Orange
    } else {
      return "#ef4444"; // High criticality - Red
    }
  };

  const getCategoryLabel = () => {
    // Prefer azure_service_name, fallback to azure_service_category
    if (data.azure_service_name) {
      return data.azure_service_name;
    }
    
    if (data.azure_service_category) {
      return data.azure_service_category;
    }
    
    // Fallback to type-based categorization
    switch (data.type) {
      case "vm":
      case "aks":
        return "Execution";
      case "storage":
      case "sql":
      case "keyvault":
        return "Integration";
      case "vnet":
      case "subnet":
      case "nsg":
      case "network":
      case "nic":
      case "pip":
        return "Preparation";
      default:
        return "Resource";
    }
  };

  const categoryColor = getCategoryColor();
  const categoryLabel = getCategoryLabel();

  // Get confidence and element weight from metadata
  const confidence = data.confidence !== undefined 
    ? `${Math.round((data.confidence as number) * 100)}%` 
    : "N/A";
  
  const elementWeight = data.element_weight !== undefined
    ? `${data.element_weight.toFixed(2)}%`
    : "N/A";

  return (
    <>
      {(data.groupId && data.onRemoveFromGroup) || data.onRemoveFromGraph ? (
        <div
          onClick={(e) => {
            e.stopPropagation();
            if (data.groupId && data.onRemoveFromGroup) {
              data.onRemoveFromGroup();
            } else if (data.onRemoveFromGraph) {
              data.onRemoveFromGraph();
            }
          }}
          style={{
            position: "absolute",
            top: "-3px",
            right: "-3px",
            background: "#7c3aed",
            color: "#fff",
            padding: "2px 5px",
            borderRadius: "8px",
            fontSize: "8px",
            fontWeight: 700,
            border: "2px solid #1a1a1a",
            cursor: "pointer",
            userSelect: "none",
            zIndex: 10
          }}
          title={data.groupId ? "Click to remove from group" : "Click to remove from workload"}
        >
          ✕
        </div>
      ) : null}

      <div
        ref={nodeRef}
        style={{
          borderRadius: "12px",
          background: "#ffffff",
          border: selected ? "3px solid #f59e0b" : "2px solid #b7b8baff",
          width: "180px",
          textAlign: "center",
          fontFamily: "Segoe UI, system-ui, sans-serif",
          boxShadow: selected 
            ? "0 0 20px rgba(245, 158, 11, 0.6)" 
            : "0 4px 12px rgba(0,0,0,0.15)",
          cursor: "pointer",
          transition: "all 0.2s ease",
          position: "relative",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column"
        }}
      >
        {isVirtual && (
          <div
            title="This resource is part of the target state and not yet deployed."
            style={{
              position: "absolute",
              top: "44px",
              left: "69px",
              border: "1px solid #6b7280",
              color: "#374151",
              background: "rgba(235,235,235,0.90)",
              padding: "2px 6px",
              borderRadius: "999px",
              fontSize: "6px",
              fontWeight: 700,
              letterSpacing: "0.6px",
              textTransform: "uppercase",
              zIndex: 12
            }}
          >
            VIRTUAL
          </div>
        )}
        {/* Category header */}
        <div style={{
          background: categoryColor,
          color: "#ffffff",
          padding: "6px 12px",
          fontSize: "10px",
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: "0.5px"
        }}>
          {categoryLabel}
        </div>

        {data.ai_annotation && (
          <div
            style={{
              position: "absolute",
              top: "32px",
              right: "8px",
              background: "#f59e0b",
              color: "#000",
              padding: "2px 5px",
              borderRadius: "8px",
              fontSize: "8px",
              fontWeight: 700,
              border: "2px solid #1a1a1a",
              zIndex: 10
            }}
          >
            AI
          </div>
        )}

        {data.user_customized && (
          <div
            style={{
              position: "absolute",
              top: "32px",
              left: "8px",
              background: "#2ea043",
              color: "#fff",
              padding: "2px 5px",
              borderRadius: "8px",
              fontSize: "8px",
              fontWeight: 700,
              border: "2px solid #1a1a1a",
              zIndex: 10
            }}
            title="User input"
          >
            Ui
          </div>
        )}

        {/* Icon container - show resilience circle if available, otherwise gray circle */}
        <div style={{
          padding: "10px 10px 5px",
          display: "flex",
          justifyContent: "center",
          alignItems: "center"
        }}>
          {data.metadata && (data.metadata as any).resilience?.resilience_score !== undefined ? (
            <ResilienceCircle 
              score={resilience?.score ?? 0}
              size={80}
            >
              {iconError ? (
                <span role="img" aria-label="fallback icon" style={{ fontSize: "32px" }}>
                  📋
                </span>
              ) : (
                <img
                  src={iconUrl}
                  alt={data.type || "resource"}
                  onError={() => setIconError(true)}
                  style={{
                    width: "48px",
                    height: "48px",
                    objectFit: "contain"
                  }}
                />
              )}
            </ResilienceCircle>
          ) : (
            <div style={{
              position: "relative",
              width: "80px",
              height: "80px",
              borderRadius: "50%",
              background: "#b7b8baff",
              padding: "4px",
              display: "flex",
              justifyContent: "center",
              alignItems: "center"
            }}>
              <div style={{
                width: "100%",
                height: "100%",
                borderRadius: "50%",
                background: "#ffffff",
                display: "flex",
                justifyContent: "center",
                alignItems: "center"
              }}>
                {iconError ? (
                  <span role="img" aria-label="fallback icon" style={{ fontSize: "32px" }}>
                    📋
                  </span>
                ) : (
                  <img
                    src={iconUrl}
                    alt={data.type || "resource"}
                    onError={() => setIconError(true)}
                    style={{
                      width: "48px",
                      height: "48px",
                      objectFit: "contain"
                    }}
                  />
                )}
              </div>
            </div>
          )}
        </div>

        {/* Resource name */}
        <div style={{ 
          padding: "0 0px 2px",
          fontSize: "11px",
          fontWeight: 600,
          color: "#1f2937",
          wordBreak: "break-word",
          lineHeight: "1.3",
          minHeight: "28px"
        }}>
          {data.label}
        </div>

        {/* Metrics row */}
        <div style={{
          display: "flex",
          justifyContent: "space-around",
          alignItems: "center",
          padding: "2px 0px 5px",
          gap: "8px"
        }}>
          <div style={{ textAlign: "center" }}>
            <div style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: "4px"
            }}>
              <ConfidenceIcon size={14} />
              <span style={{ fontSize: "12px", fontWeight: 600, color: "#1f2937" }}>
                {confidence}
              </span>
            </div>
          </div>
          <div style={{ textAlign: "center" }}>
            <div style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: "4px"
            }}>
              <WeightIcon size={14} />
              <span 
                ref={weightRef}
                style={{ fontSize: "12px", fontWeight: 600, color: "#1f2937", cursor: "help" }}
                onMouseEnter={() => {
                  if (data.ai_tooltip) setShowWeightTooltip(true);
                }}
                onMouseLeave={() => setShowWeightTooltip(false)}
              >
                {elementWeight}
              </span>
            </div>
          </div>
        </div>
      
      {/* Handles on all sides - can be both source and target */}
      <Handle 
        id="top"
        type="target"
        position={Position.Top} 
        isConnectable={isConnectable}
        style={{ background: "#0078d4", width: "8px", height: "8px" }}
      />
      <Handle 
        id="bottom"
        type="source"
        position={Position.Bottom} 
        isConnectable={isConnectable}
        style={{ background: "#0078d4", width: "8px", height: "8px" }}
      />
      <Handle 
        id="left"
        type="target"
        position={Position.Left} 
        isConnectable={isConnectable}
        style={{ background: "#0078d4", width: "8px", height: "8px" }}
      />
      <Handle 
        id="right"
        type="source"
        position={Position.Right} 
        isConnectable={isConnectable}
        style={{ background: "#0078d4", width: "8px", height: "8px" }}
      />
    </div>

    {data.ai_tooltip && showWeightTooltip && createPortal(
      <div
        style={{
          position: "fixed",
          left: `${weightTooltipPosition.x}px`,
          top: `${weightTooltipPosition.y}px`,
          transform: "translateY(-50%)",
          minWidth: "200px",
          maxWidth: "280px",
          background: "#111827",
          color: "#f9fafb",
          border: "1px solid #374151",
          borderRadius: "8px",
          padding: "12px",
          boxShadow: "0 10px 25px rgba(0,0,0,0.5)",
          fontSize: "11px",
          textAlign: "left",
          zIndex: 99999,
          pointerEvents: "none",
          lineHeight: 1.4
        }}
      >
        {(() => {
          const reasonItem = data.ai_tooltip.items.find(item => item.label.toLowerCase() === "reason");
          return reasonItem ? (
            <div style={{ lineHeight: 1.5 }}>
              {reasonItem.value}
            </div>
          ) : (
            <div style={{ color: "#9CA3AF" }}>No reasoning available</div>
          );
        })()}
      </div>,
      document.body
    )}
    </>
  );
};

export default AzureNode;
