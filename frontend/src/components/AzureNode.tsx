import React, { useState, useRef, useEffect } from "react";
import { createPortal } from "react-dom";
import { Handle, Position } from "reactflow";
import { getAzureIcon } from "../utils/azureIcons";
import type { AiTooltip } from "../domain/graphView";

interface AzureNodeProps {
  data: {
    label: string;
    icon?: string;
    type?: string;
    criticality_stars?: string;
    color?: string;
    ai_annotation?: boolean;
    user_customized?: boolean;
    ai_tooltip?: AiTooltip;
    user_tooltip?: AiTooltip;
    groupId?: string;
    onRemoveFromGroup?: () => void;
  };
  isConnectable: boolean;
  selected: boolean;
}

const AzureNode: React.FC<AzureNodeProps> = ({ data, isConnectable, selected }) => {
  const [iconError, setIconError] = useState(false);
  const [showTooltip, setShowTooltip] = useState(false);
  const [tooltipPosition, setTooltipPosition] = useState({ x: 0, y: 0 });
  const nodeRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (showTooltip && nodeRef.current) {
      const rect = nodeRef.current.getBoundingClientRect();
      setTooltipPosition({
        x: rect.left + rect.width / 2,
        y: rect.bottom + 12
      });
    }
  }, [showTooltip]);

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
  const isLightColor = ["#50e6ff", "#b3e0ff", "#00bcf2", "#5EA0EF", "#ffffff"].includes(nodeColor);
  const textColor = isLightColor ? "#000000" : "#ffffff";
  const borderColor = isLightColor ? "#0078d4" : "#333";
  const iconUrl = data.icon || getAzureIcon(data.type || "resource");

  return (
    <>
      <div
        ref={nodeRef}
        style={{
          padding: "10px 12px",
          borderRadius: "8px",
          background: nodeColor,
          color: textColor,
          border: selected ? "3px solid #f59e0b" : `2px solid ${borderColor}`,
          minWidth: "110px",
          maxWidth: "140px",
          textAlign: "center",
          fontSize: "11px",
          fontWeight: 600,
          fontFamily: "Segoe UI, system-ui, sans-serif",
          marginTop: "15px",
          boxShadow: selected 
            ? "0 0 12px rgba(245, 158, 11, 0.6)" 
            : "0 2px 8px rgba(0,0,0,0.25)",
          cursor: "pointer",
          transition: "all 0.2s ease",
          position: "relative"
        }}
      onMouseEnter={() => {
        if (data.ai_tooltip || data.user_tooltip) setShowTooltip(true);
      }}
      onMouseLeave={() => setShowTooltip(false)}
    >
      {data.ai_annotation && (
        <div
          style={{
            position: "absolute",
            top: "-6px",
            right: "-6px",
            background: "#f59e0b",
            color: "#000",
            padding: "2px 5px",
            borderRadius: "8px",
            fontSize: "8px",
            fontWeight: 700,
            border: "2px solid #1a1a1a"
          }}
        >
          AI
        </div>
      )}

      {data.user_customized && (
        <div
          style={{
            position: "absolute",
            top: "-6px",
            left: "-6px",
            background: "#2ea043",
            color: "#fff",
            padding: "2px 5px",
            borderRadius: "8px",
            fontSize: "8px",
            fontWeight: 700,
            border: "2px solid #1a1a1a"
          }}
          title="User input"
        >
          Ui
        </div>
      )}

      {data.groupId && data.onRemoveFromGroup && (
        <div
          onClick={(e) => {
            e.stopPropagation();
            setShowTooltip(false);
            data.onRemoveFromGroup?.();
          }}
          style={{
            position: "absolute",
            bottom: "-6px",
            right: "-6px",
            background: "#7c3aed",
            color: "#fff",
            padding: "2px 5px",
            borderRadius: "8px",
            fontSize: "8px",
            fontWeight: 700,
            border: "2px solid #1a1a1a",
            cursor: "pointer",
            userSelect: "none"
          }}
          title="Click to remove from group"
        >
          ✕
        </div>
      )}
      
      <div style={{ marginBottom: "6px", fontSize: "24px" }}>
        {iconError ? (
          <span role="img" aria-label="fallback icon" style={{ fontSize: "22px" }}>
            📋
          </span>
        ) : (
          <img
            src={iconUrl}
            alt={data.type || "resource"}
            onError={() => setIconError(true)}
            style={{
              width: "32px",
              height: "32px",
              objectFit: "contain",
              filter: "none"
            }}
          />
        )}
      </div>
      
      <div style={{ 
        wordBreak: "break-word", 
        lineHeight: "1.3", 
        marginBottom: "4px",
        minHeight: "28px",
        display: "flex",
        alignItems: "center",
        justifyContent: "center"
      }}>
        {data.label}
      </div>
      
      {data.criticality_stars && (
        <div style={{ 
          fontSize: "16px", 
          marginTop: "4px", 
          color: textColor,
          letterSpacing: "1px"
        }}>
          {data.criticality_stars}
        </div>
      )}
      
      <Handle 
        id="t"
        position={Position.Top} 
        type="target" 
        isConnectable={isConnectable}
        style={{ background: borderColor, width: "8px", height: "8px" }}
      />
      <Handle 
        id="b"
        position={Position.Bottom} 
        type="source" 
        isConnectable={isConnectable}
        style={{ background: borderColor, width: "8px", height: "8px" }}
      />
      <Handle 
        id="l"
        position={Position.Left} 
        type="target" 
        isConnectable={isConnectable}
        style={{ background: borderColor, width: "8px", height: "8px" }}
      />
      <Handle 
        id="r"
        position={Position.Right} 
        type="source" 
        isConnectable={isConnectable}
        style={{ background: borderColor, width: "8px", height: "8px" }}
      />
    </div>

    {(data.ai_tooltip || data.user_tooltip) && showTooltip && createPortal(
      <div
        style={{
          position: "fixed",
          left: `${tooltipPosition.x}px`,
          top: `${tooltipPosition.y}px`,
          transform: "translateX(-50%)",
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
        {data.user_tooltip && (
          <div style={{ marginBottom: data.ai_tooltip ? 10 : 0 }}>
            <div style={{ fontWeight: 700, marginBottom: 8 }}>{data.user_tooltip.title}</div>
            {data.user_tooltip.items.map((item, idx) => (
              <div key={`u-${idx}`} style={{ marginBottom: 4 }}>
                <span style={{ color: "#9CA3AF" }}>{item.label}:</span> {item.value}
              </div>
            ))}
          </div>
        )}

        {data.ai_tooltip && (
          <div>
            <div style={{ fontWeight: 700, marginBottom: 8 }}>{data.ai_tooltip.title}</div>
            {data.ai_tooltip.items.map((item, idx) => (
              <div key={`a-${idx}`} style={{ marginBottom: 4 }}>
                <span style={{ color: "#9CA3AF" }}>{item.label}:</span> {item.value}
              </div>
            ))}
          </div>
        )}
      </div>,
      document.body
    )}
    </>
  );
};

export default AzureNode;
