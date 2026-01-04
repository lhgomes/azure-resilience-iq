import React from "react";
import type { ViewLevel } from "../domain/graphView";

interface ResourceGroupOption {
  key: string;
  label: string;
}

interface ServiceOptionCategory {
  category: string;
  services: Array<{ key: string; label: string }>;
}

interface Props {
  viewLevel: ViewLevel;
  onViewLevelChange: (next: ViewLevel) => void;

  aiLayerEnabled: boolean;
  onAiLayerEnabledChange: (next: boolean) => void;

  userLayerEnabled: boolean;
  onUserLayerEnabledChange: (next: boolean) => void;

  resourceGroupOptions: ResourceGroupOption[];
  resourceGroupFilter: Set<string>;
  onResourceGroupFilterChange: (next: Set<string>) => void;

  serviceOptions: ServiceOptionCategory[];
  serviceFilter: Set<string>;
  onServiceFilterChange: (next: Set<string>) => void;

  expandedCategories: Set<string>;
  onExpandedCategoriesChange: (next: Set<string>) => void;

  showLegend: boolean;
  onToggleLegend: () => void;
}

const WorkloadSidebar: React.FC<Props> = props => {
  const showResourceGroupFilter = props.resourceGroupOptions.length >= 2;

  return (
    <div
      style={{
        padding: 16,
        overflowY: "auto",
        overflowX: "hidden",
        flex: 1,
      }}
    >
      <h3 style={{ margin: "0 0 20px 0", color: "#eee", fontSize: 16 }}>Controls</h3>

      <div style={{ marginBottom: 20 }}>
        <label style={{ display: "block", fontSize: 12, color: "#9AA0A6", marginBottom: 8 }}>
          View Level
        </label>
        <select
          value={props.viewLevel}
          onChange={e => props.onViewLevelChange(e.target.value as ViewLevel)}
          style={{
            width: "100%",
            background: "#181818",
            color: "#fff",
            border: "1px solid #333",
            padding: "8px",
            borderRadius: 4,
          }}
        >
          <option value="overview">Overview (L0)</option>
          <option value="network">Network (L1)</option>
          <option value="full">Full (L2)</option>
        </select>
      </div>

      <div style={{ marginBottom: 20 }}>
        <label style={{ display: "block", fontSize: 12, color: "#9AA0A6", marginBottom: 8 }}>
          Detail View
        </label>
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            color: "#eee",
            fontSize: 13,
            marginBottom: 8,
            cursor: "pointer",
          }}
        >
          <input
            type="checkbox"
            checked={props.aiLayerEnabled}
            onChange={e => props.onAiLayerEnabledChange(e.target.checked)}
          />
          AI layer
        </label>

        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            color: "#eee",
            fontSize: 13,
            cursor: "pointer",
          }}
        >
          <input
            type="checkbox"
            checked={props.userLayerEnabled}
            onChange={e => props.onUserLayerEnabledChange(e.target.checked)}
          />
          User overrides
        </label>
      </div>

      {showResourceGroupFilter && (
        <div style={{ marginBottom: 20 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <label style={{ fontSize: 12, color: "#9AA0A6" }}>Resource Groups</label>
            <button
              onClick={() => {
                const allGroups = props.resourceGroupOptions.map(rg => rg.key);
                props.onResourceGroupFilterChange(new Set(allGroups));
              }}
              style={{
                background: "transparent",
                border: "none",
                color: "#85a2c6ff",
                cursor: "pointer",
                fontSize: 11,
                textDecoration: "underline",
              }}
            >
              Select All
            </button>
          </div>
          <div
            style={{
              maxHeight: 200,
              overflowY: "auto",
              border: "1px solid #333",
              borderRadius: 4,
              padding: 8,
              background: "#181818",
            }}
          >
            {props.resourceGroupOptions.map(rg => (
              <label
                key={rg.key}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "4px 0",
                  cursor: "pointer",
                  fontSize: 12,
                  color: "#ddd",
                }}
              >
                <input
                  type="checkbox"
                  checked={props.resourceGroupFilter.has(rg.key)}
                  onChange={e => {
                    const next = new Set(props.resourceGroupFilter);
                    if (e.target.checked) next.add(rg.key);
                    else next.delete(rg.key);
                    props.onResourceGroupFilterChange(next);
                  }}
                />
                {rg.label}
              </label>
            ))}
          </div>
        </div>
      )}

      <div style={{ marginBottom: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <label style={{ fontSize: 12, color: "#9AA0A6" }}>Services</label>
          <button
            onClick={() => {
              const allServices = props.serviceOptions.flatMap(cat => cat.services.map(s => s.key));
              props.onServiceFilterChange(new Set(allServices));
            }}
            style={{
              background: "transparent",
              border: "none",
              color: "#85a2c6ff",
              cursor: "pointer",
              fontSize: 11,
              textDecoration: "underline",
            }}
          >
            Select All
          </button>
        </div>
        <div
          style={{
            maxHeight: 400,
            overflowY: "auto",
            border: "1px solid #333",
            borderRadius: 4,
            padding: 8,
            background: "#181818",
          }}
        >
          {props.serviceOptions.map(category => {
            const allServicesInCategory = category.services.map(s => s.key);
            const selectedServicesInCategory = allServicesInCategory.filter(key => props.serviceFilter.has(key));
            const isExpanded = props.expandedCategories.has(category.category);
            const allSelected = selectedServicesInCategory.length === allServicesInCategory.length;
            const someSelected = selectedServicesInCategory.length > 0 && !allSelected;

            return (
              <div key={category.category} style={{ marginBottom: 8 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                  <button
                    onClick={() => {
                      const next = new Set(props.expandedCategories);
                      if (isExpanded) next.delete(category.category);
                      else next.add(category.category);
                      props.onExpandedCategoriesChange(next);
                    }}
                    style={{
                      background: "transparent",
                      border: "none",
                      color: "#9AA0A6",
                      cursor: "pointer",
                      fontSize: 14,
                      padding: 0,
                      width: 16,
                      height: 16,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    {isExpanded ? "▼" : "▶"}
                  </button>

                  <input
                    type="checkbox"
                    checked={allSelected}
                    ref={el => {
                      if (el) el.indeterminate = someSelected;
                    }}
                    onChange={e => {
                      const newFilter = new Set(props.serviceFilter);
                      if (e.target.checked) allServicesInCategory.forEach(key => newFilter.add(key));
                      else allServicesInCategory.forEach(key => newFilter.delete(key));
                      props.onServiceFilterChange(newFilter);
                    }}
                    style={{ cursor: "pointer" }}
                  />

                  <span
                    style={{ fontSize: 13, fontWeight: 600, color: "#eee", cursor: "pointer" }}
                    onClick={() => {
                      const next = new Set(props.expandedCategories);
                      if (isExpanded) next.delete(category.category);
                      else next.add(category.category);
                      props.onExpandedCategoriesChange(next);
                    }}
                  >
                    {category.category} ({category.services.length})
                  </span>
                </div>

                {isExpanded && (
                  <div style={{ marginLeft: 24 }}>
                    {category.services.map(service => (
                      <label
                        key={service.key}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          padding: "4px 0",
                          cursor: "pointer",
                          fontSize: 12,
                          color: "#ddd",
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={props.serviceFilter.has(service.key)}
                          onChange={e => {
                            const next = new Set(props.serviceFilter);
                            if (e.target.checked) next.add(service.key);
                            else next.delete(service.key);
                            props.onServiceFilterChange(next);
                          }}
                        />
                        {service.label}
                      </label>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <button
        onClick={props.onToggleLegend}
        title="Show/hide visual legend"
        style={{
          width: "100%",
          padding: "8px 12px",
          background: props.showLegend ? "#1a1a2e" : "#161616",
          color: props.showLegend ? "#f59e0b" : "#9AA0A6",
          border: props.showLegend ? "1px solid #f59e0b" : "1px solid #333",
          borderRadius: 4,
          cursor: "pointer",
          fontSize: 13,
          marginBottom: 20,
        }}
      >
        {props.showLegend ? "Hide Legend" : "Show Legend"}
      </button>
    </div>
  );
};

export default WorkloadSidebar;
