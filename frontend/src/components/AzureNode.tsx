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
    criticality_score?: number;
    confidence?: number;
    color?: string;
    azure_service_category?: string;
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

  // Services covered by Azure APRL (Azure Proactive Resiliency Library)
  // https://github.com/Azure/Azure-Proactive-Resiliency-Library-v2
  // Resources NOT in this list are fully managed by Azure (blue header)
  const isCoveredByAPRL = () => {
    const aprlCoveredTypes = [
      // Compute
      "vm", // Virtual Machines
      "vmss", // Virtual Machine Scale Sets
      "aks", // Azure Kubernetes Service
      "app_service", // App Service
      "function_app", // Azure Functions
      "container_instances", // Azure Container Instances
      "container_registry", // Azure Container Registry (ACR)
      "batch", // Azure Batch
      "service_fabric", // Service Fabric
      "avs", // Azure VMware Solution
      
      // Storage
      "storage", // Storage Accounts
      "netapp", // Azure NetApp Files
      "disk", // Managed Disks
      "recovery_services_vault", // Recovery Services Vault
      
      // Databases
      "sql", // Azure SQL Database
      "sql_managed_instance", // SQL Managed Instance
      "cosmos_db", // Cosmos DB
      "mysql", // Azure Database for MySQL
      "postgresql", // Azure Database for PostgreSQL
      "mariadb", // Azure Database for MariaDB
      "redis", // Azure Cache for Redis
      "synapse", // Azure Synapse Analytics
      
      // Networking
      "vnet", // Virtual Network
      "nic", // Network Interface
      "vpn_gateway", // VPN Gateway
      "expressroute", // ExpressRoute
      "application_gateway", // Application Gateway
      "load_balancer", // Load Balancer
      "traffic_manager", // Traffic Manager
      "front_door", // Azure Front Door
      "firewall", // Azure Firewall
      "bastion", // Azure Bastion
      "nat_gateway", // NAT Gateway
      "pip", // Public IP Addresses
      "private_endpoint", // Private Endpoints
      "virtual_wan", // Virtual WAN
      "dns", // Azure DNS
      
      // Security & Identity
      "keyvault", // Key Vault
      "app_gateway_waf", // Application Gateway WAF
      
      // Integration
      "api_management", // API Management
      "service_bus", // Service Bus
      "event_hub", // Event Hubs
      "event_grid", // Event Grid
      "logic_apps", // Logic Apps
      
      // AI + Machine Learning
      "cognitive_services", // Cognitive Services
      "machine_learning", // Azure Machine Learning
      "search", // Azure AI Search
      "openai", // Azure OpenAI
      
      // Analytics
      "data_factory", // Data Factory
      "databricks", // Azure Databricks
      "stream_analytics", // Stream Analytics
      "hdinsight", // HDInsight
      "analysis_services", // Analysis Services
      
      // Management & Governance
      "automation", // Azure Automation
      "backup", // Azure Backup
      "site_recovery", // Azure Site Recovery
      "monitor", // Azure Monitor
      
      // Web
      "cdn", // Azure CDN
      "static_web_apps", // Static Web Apps
      
      // IoT
      "iot_hub", // IoT Hub
      "notification_hubs", // Notification Hubs
    ];
    
    return aprlCoveredTypes.includes(data.type || "");
  };

  // Category color based on APRL coverage and criticality
  // - Resources NOT covered by APRL: Light Gray (Azure fully manages resilience)
  // - Resources covered by APRL: Color based on criticality (customer has actions to take)
  const getCategoryColor = () => {
    // Resources NOT covered by APRL are fully managed by Azure
    if (!isCoveredByAPRL()) {
      return "#b7b8baff"; // Light Gray - fully managed by Azure, no APRL guidance needed
    }
    
    // Resources covered by APRL use criticality-based colors
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
    // Use azure_service_category from AI annotations if available
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

  // Gradient colors for the ring around the icon
  const getGradientColors = () => {
    switch (data.type) {
      case "vm":
      case "aks":
        return { start: "#FF6B6B", mid: "#4ECDC4", end: "#45B7D1" };
      case "storage":
      case "sql":
      case "keyvault":
        return { start: "#FFB347", mid: "#FFCC33", end: "#FFA07A" };
      case "vnet":
      case "subnet":
      case "nsg":
        return { start: "#9B59B6", mid: "#E91E63", end: "#FF9800" };
      default:
        return { start: "#667EEA", mid: "#764BA2", end: "#F093FB" };
    }
  };

  const gradientColors = getGradientColors();
  const categoryColor = getCategoryColor();
  const categoryLabel = getCategoryLabel();
  const categoryTextColor = !isCoveredByAPRL() ? "#111827" : "#ffffff";

  // Get confidence and criticality from metadata
  const confidence = data.confidence !== undefined 
    ? `${Math.round((data.confidence as number) * 100)}%` 
    : "N/A";
  
  const criticality = data.criticality_score !== undefined
    ? `${data.criticality_score}/10`
    : "N/A";

  return (
    <>
      {data.groupId && data.onRemoveFromGroup && (
        <div
          onClick={(e) => {
            e.stopPropagation();
            setShowTooltip(false);
            data.onRemoveFromGroup?.();
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
          title="Click to remove from group"
        >
          ✕
        </div>
      )}

      <div
        ref={nodeRef}
        style={{
          borderRadius: "12px",
          background: "#ffffff",
          border: selected ? "3px solid #f59e0b" : "2px solid #e5e7eb",
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
        onMouseEnter={() => {
          if (data.ai_tooltip || data.user_tooltip) setShowTooltip(true);
        }}
        onMouseLeave={() => setShowTooltip(false)}
      >
        {/* Category header */}
        <div style={{
          background: categoryColor,
          color: categoryTextColor,
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

        {/* Icon container with gradient ring */}
        <div style={{
          padding: "10px 10px 5px",
          display: "flex",
          justifyContent: "center",
          alignItems: "center"
        }}>
          <div style={{
            position: "relative",
            width: "80px",
            height: "80px",
            borderRadius: "50%",
            background: `conic-gradient(from 0deg, ${gradientColors.start}, ${gradientColors.mid}, ${gradientColors.end}, ${gradientColors.start})`,
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
              <span style={{ fontSize: "10px", color: "#6b7280" }}>◎</span>
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
              <span style={{ fontSize: "10px", color: "#6b7280" }}>⚡</span>
              <span style={{ fontSize: "12px", fontWeight: 600, color: "#1f2937" }}>
                {criticality}
              </span>
            </div>
          </div>
        </div>
      
      <Handle 
        id="t"
        position={Position.Top} 
        type="target" 
        isConnectable={isConnectable}
        style={{ background: "#0078d4", width: "8px", height: "8px" }}
      />
      <Handle 
        id="b"
        position={Position.Bottom} 
        type="source" 
        isConnectable={isConnectable}
        style={{ background: "#0078d4", width: "8px", height: "8px" }}
      />
      <Handle 
        id="l"
        position={Position.Left} 
        type="target" 
        isConnectable={isConnectable}
        style={{ background: "#0078d4", width: "8px", height: "8px" }}
      />
      <Handle 
        id="r"
        position={Position.Right} 
        type="source" 
        isConnectable={isConnectable}
        style={{ background: "#0078d4", width: "8px", height: "8px" }}
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
