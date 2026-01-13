import React, { useState } from "react";

interface TabsProps {
  tabs: Array<{
    label: string;
    content: React.ReactNode;
    icon?: string;
  }>;
  defaultTab?: number;
}

const TabbedView: React.FC<TabsProps> = ({ tabs, defaultTab = 0 }) => {
  const [activeTab, setActiveTab] = useState(defaultTab);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Tab Headers */}
      <div
        style={{
          display: "flex",
          borderBottom: "2px solid #e5e7eb",
          background: "#f9fafb",
          gap: "0",
        }}
      >
        {tabs.map((tab, idx) => (
          <button
            key={idx}
            onClick={() => setActiveTab(idx)}
            style={{
              padding: "12px 20px",
              border: "none",
              background: activeTab === idx ? "#fff" : "transparent",
              color: activeTab === idx ? "#0078d4" : "#6b7280",
              cursor: "pointer",
              fontSize: "13px",
              fontWeight: activeTab === idx ? 600 : 500,
              borderBottom: activeTab === idx ? "3px solid #0078d4" : "none",
              marginBottom: "-2px",
              transition: "all 0.2s ease",
              display: "flex",
              alignItems: "center",
              gap: "6px",
            }}
            title={tab.label}
          >
            {tab.icon && <span>{tab.icon}</span>}
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div
        style={{
          flex: 1,
          overflow: "auto",
          background: "#fff",
        }}
      >
        {tabs[activeTab] && tabs[activeTab].content}
      </div>
    </div>
  );
};

export default TabbedView;
