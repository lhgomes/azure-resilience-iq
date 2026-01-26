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
          <strong>Resiliency Score Colors:</strong>
          <div style={{ marginTop: 4, color: "#9AA0A6" }}>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#22c55e" }}>⬤ Green: 0.90-1.00 (Excellent)</span>
            </div>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#eab308" }}>⬤ Yellow: 0.75-0.89 (Good)</span>
            </div>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#f97316" }}>⬤ Orange: 0.50-0.74 (Fair)</span>
            </div>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#ef4444" }}>⬤ Red: &lt;0.50 (Poor)</span>
            </div>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#b7b8baff" }}>⬤ Gray: No APRL coverage</span>
            </div>
          </div>
        </div>

        <div style={{ marginBottom: 12 }}>
          <strong>Node Criticality (LLM-assigned):</strong>
          <div style={{ marginTop: 4, color: "#9AA0A6", fontSize: 11 }}>
            Resources are scored 1-10 by LLM analysis. Higher criticality increases weight in resilience calculations.
          </div>
        </div>

        <div style={{ marginBottom: 12 }}>
          <strong>Edge Colors (Multi-Source Signals):</strong>
          <div style={{ marginTop: 4, color: "#9AA0A6" }}>
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#5EA0EF" }}>━ Blue: ARM Declared (Azure Resource Manager)</span>
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
            <div style={{ marginBottom: 3 }}>
              <span style={{ color: "#d946ef" }}>━ Magenta: Bridge (hidden node bypass)</span>
            </div>
            <div style={{ marginTop: 6, fontSize: 11 }}>
              12 signal types: ARM_Declared, PrivateEndpoint, FlowLog, AppInsights, ConnectionString, AppConfig, PrivateDNS, SubnetRouting, DNSZoneLink, RouteTable, VNetCoupling, NSGRule
            </div>
          </div>
        </div>

        <div style={{ marginBottom: 12 }}>
          <strong>Node Metrics:</strong>
          <div style={{ marginTop: 4, color: "#9AA0A6" }}>
            <div style={{ marginBottom: 3 }}>◎ Confidence (AI analysis quality)</div>
            <div style={{ marginBottom: 3 }}>⚡ Criticality score (1-10)</div>
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

        <div style={{ marginBottom: 12 }}>
          <strong>Resiliency Scoring:</strong>
          <div style={{ marginTop: 4, color: "#9AA0A6", fontSize: 11 }}>
            <div style={{ marginBottom: 4 }}>
              Score = Passed Checks Weight / Total Checks Weight
            </div>
            <div style={{ marginBottom: 4 }}>
              Weight = Element Weight × Category Weight × Impact Weight
            </div>
            <div>
              Configured in app_config.yaml. See README for details.
            </div>
          </div>
        </div>

        <div style={{ marginBottom: 12 }}>
          <strong>Resiliency Groups:</strong>
          <div style={{ marginTop: 4, color: "#9AA0A6", fontSize: 11 }}>
            Auto-detected groups: Availability Sets, VMSS, Load Balancer backends, Storage geo-redundancy, SQL failover groups, Cosmos replication, and custom groups. Resources evaluated in group context.
          </div>
        </div>

        <div style={{ marginBottom: 0 }}>
          <strong>APRL Coverage:</strong>
          <div style={{ marginTop: 4, color: "#9AA0A6", fontSize: 11 }}>
            Services covered by Azure Proactive Resiliency Library show resilience score colors. Non-covered services (Azure-managed) show gray.
          </div>
        </div>
      </div>
    </div>
  );
};

export default LegendPanel;
