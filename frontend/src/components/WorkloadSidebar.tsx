import React from "react";
import type { ViewLevel } from "../domain/graphView";
import { AddRegular, CheckmarkRegular, DeleteRegular, DismissRegular, EditRegular, Save16Regular } from "@fluentui/react-icons";

export interface SubscriptionOption {
  id: string;
  name: string;
}

interface ResourceGroupOption {
  key: string;
  label: string;
}

interface ServiceOptionCategory {
  category: string;
  services: Array<{ key: string; label: string }>;
}

interface Props {
  subscriptions: SubscriptionOption[];
  selectedSubscriptions: Set<string>;
  onSelectedSubscriptionsChange: (next: Set<string>) => void;

  workloads: Array<{ workload_id: string; name: string; updated_at?: string }>;
  activeWorkloadId: string | null;
  workloadName: string;
  onWorkloadNameChange: (next: string) => void;
  onWorkloadSelect: (workloadId: string | null) => void;
  onWorkloadCreate: () => void;
  onWorkloadSave: () => void;
  onWorkloadRename: () => void;
  onWorkloadDelete: () => void;
  workloadError?: string | null;
  workloadDirty?: boolean;
  workloadNewDirty?: boolean;

  viewLevel: ViewLevel;
  onViewLevelChange: (next: ViewLevel) => void;

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
  const hasSelection = props.selectedSubscriptions.size > 0;

  // Shared button base styles
  const buttonBase: React.CSSProperties = {
    padding: "4px 12px",
    fontSize: 13,
    fontWeight: 400,
    borderRadius: 2,
    cursor: "pointer",
    border: "1px solid",
    transition: "all 0.1s ease-in-out",
    outline: "none",
    lineHeight: "20px",
  };

  const iconButton: React.CSSProperties = {
    padding: "6px 8px",
    fontSize: 16,
    fontWeight: 400,
    borderRadius: 2,
    cursor: "pointer",
    border: "1px solid transparent",
    background: "transparent",
    transition: "all 0.1s ease-in-out",
    outline: "none",
    minWidth: 32,
    height: 32,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  };

  const primaryButton: React.CSSProperties = {
    ...buttonBase,
    background: "#0078d4",
    color: "#fff",
    borderColor: "#0078d4",
  };

  const secondaryButton: React.CSSProperties = {
    ...buttonBase,
    background: "transparent",
    color: "#0078d4",
    borderColor: "#8a8886",
  };

  const dangerButton: React.CSSProperties = {
    ...buttonBase,
    background: "transparent",
    color: "#a4262c",
    borderColor: "#8a8886",
  };

  const disabledButton: React.CSSProperties = {
    ...buttonBase,
    background: "#f3f2f1",
    color: "#a19f9d",
    borderColor: "#c8c6c4",
    cursor: "not-allowed",
  };

  return (
    <div
      style={{
        padding: "16px 12px",
        overflowY: "auto",
        overflowX: "hidden",
        color: "#323130",
      }}
    >
      {/* Workload Section */}
      <div style={{ marginBottom: 20 }}>
        <h3 style={{ 
          margin: "0 0 8px 0", 
          fontSize: 13, 
          fontWeight: 600,
          color: "#323130",
          textTransform: "uppercase",
          letterSpacing: "0.5px"
        }}>
          Workload
        </h3>
        
        <select
          value={props.activeWorkloadId ?? ""}
          onChange={e => props.onWorkloadSelect(e.target.value || null)}
          style={{
            width: "100%",
            background: "#fff",
            color: "#323130",
            border: "1px solid #8a8886",
            padding: "5px 8px",
            borderRadius: 2,
            fontSize: 14,
            marginBottom: 8,
            cursor: "pointer",
            outline: "none",
          }}
          onFocus={e => e.target.style.borderColor = "#0078d4"}
          onBlur={e => e.target.style.borderColor = "#8a8886"}
        >
          <option value="">Select workload</option>
          {props.workloads.map(workload => (
            <option key={workload.workload_id} value={workload.workload_id}>
              {workload.name}
            </option>
          ))}
        </select>

        <input
          type="text"
          value={props.workloadName}
          onChange={e => props.onWorkloadNameChange(e.target.value)}
          placeholder="Workload name"
          style={{
            width: "100%",
            background: "#fff",
            color: "#323130",
            border: "1px solid #8a8886",
            padding: "5px 8px",
            borderRadius: 2,
            fontSize: 14,
            marginBottom: 8,
            boxSizing: "border-box",
            outline: "none",
          }}
          onFocus={e => e.target.style.borderColor = "#0078d4"}
          onBlur={e => e.target.style.borderColor = "#8a8886"}
        />

        <div style={{ display: "flex", gap: 4 }}>
          <button
            onClick={props.onWorkloadCreate}
            disabled={!props.workloadName.trim()}
            title="Save as new workload"
            style={{
              ...iconButton,
              color: props.workloadName.trim() ? (!props.activeWorkloadId && props.workloadNewDirty ? "#fff" : "#0078d4") : "#c8c6c4",
              background: props.workloadName.trim() && !props.activeWorkloadId && props.workloadNewDirty ? "#0078d4" : "transparent",
              cursor: props.workloadName.trim() ? "pointer" : "not-allowed",
            }}
            onMouseEnter={e => {
              if (props.workloadName.trim()) {
                e.currentTarget.style.background = props.workloadNewDirty && !props.activeWorkloadId ? "#106ebe" : "#f3f2f1";
              }
            }}
            onMouseLeave={e => {
              if (props.workloadName.trim()) {
                e.currentTarget.style.background = props.workloadNewDirty && !props.activeWorkloadId ? "#0078d4" : "transparent";
              }
            }}
          >
            <AddRegular style={{ fontSize: 16 }} />
          </button>
          <button
            onClick={props.onWorkloadSave}
            disabled={!props.activeWorkloadId}
            title="Save workload"
            style={{
              ...iconButton,
              color: props.activeWorkloadId ? (props.workloadDirty ? "#fff" : "#0078d4") : "#c8c6c4",
              background: props.activeWorkloadId && props.workloadDirty ? "#107c10" : "transparent",
              cursor: props.activeWorkloadId ? "pointer" : "not-allowed",
            }}
            onMouseEnter={e => {
              if (props.activeWorkloadId) {
                e.currentTarget.style.background = props.workloadDirty ? "#0e6b0e" : "#f3f2f1";
              }
            }}
            onMouseLeave={e => {
              if (props.activeWorkloadId) {
                e.currentTarget.style.background = props.workloadDirty ? "#107c10" : "transparent";
              }
            }}
          >
            <Save16Regular style={{ fontSize: 16 }} />
          </button>
          <button
            onClick={props.onWorkloadRename}
            disabled={!props.activeWorkloadId || !props.workloadName.trim()}
            title="Rename workload"
            style={{
              ...iconButton,
              color: (props.activeWorkloadId && props.workloadName.trim()) ? "#605e5c" : "#c8c6c4",
              cursor: (props.activeWorkloadId && props.workloadName.trim()) ? "pointer" : "not-allowed",
            }}
            onMouseEnter={e => {
              if (props.activeWorkloadId && props.workloadName.trim()) {
                e.currentTarget.style.background = "#f3f2f1";
              }
            }}
            onMouseLeave={e => {
              if (props.activeWorkloadId && props.workloadName.trim()) {
                e.currentTarget.style.background = "transparent";
              }
            }}
          >
            <EditRegular style={{ fontSize: 16 }} />
          </button>
          <button
            onClick={props.onWorkloadDelete}
            disabled={!props.activeWorkloadId}
            title="Delete workload"
            style={{
              ...iconButton,
              color: props.activeWorkloadId ? "#a4262c" : "#c8c6c4",
              cursor: props.activeWorkloadId ? "pointer" : "not-allowed",
            }}
            onMouseEnter={e => {
              if (props.activeWorkloadId) {
                e.currentTarget.style.background = "#fde7e9";
              }
            }}
            onMouseLeave={e => {
              if (props.activeWorkloadId) {
                e.currentTarget.style.background = "transparent";
              }
            }}
          >
            <DeleteRegular style={{ fontSize: 16 }} />
          </button>
        </div>

        {props.workloadError && (
          <div style={{ fontSize: 12, color: "#d13438", marginTop: 6 }}>{props.workloadError}</div>
        )}
      </div>

      {/* Subscription Selector */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: "#323130", textTransform: "uppercase", letterSpacing: "0.5px" }}>Subscriptions</h3>
          <div style={{ display: "flex", gap: 4 }}>
            <button
              onClick={() => {
                const allSubscriptions = props.subscriptions.map(s => s.id);
                props.onSelectedSubscriptionsChange(new Set(allSubscriptions));
              }}
              title="Select All"
              style={{
                background: "transparent",
                border: "none",
                color: "#0078d4",
                cursor: "pointer",
                padding: "2px 4px",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
              onMouseEnter={e => e.currentTarget.style.color = "#005a9e"}
              onMouseLeave={e => e.currentTarget.style.color = "#0078d4"}
            >
              <CheckmarkRegular style={{ fontSize: 16 }} />
            </button>
            <button
              onClick={() => {
                props.onSelectedSubscriptionsChange(new Set());
              }}
              title="Uncheck All"
              style={{
                background: "transparent",
                border: "none",
                color: "#a4262c",
                cursor: "pointer",
                padding: "2px 4px",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
              onMouseEnter={e => e.currentTarget.style.color = "#750b1c"}
              onMouseLeave={e => e.currentTarget.style.color = "#a4262c"}
            >
              <DismissRegular style={{ fontSize: 16 }} />
            </button>
          </div>
        </div>
        <div
          style={{
            maxHeight: 180,
            overflowY: "auto",
            border: "1px solid #8a8886",
            borderRadius: 2,
            padding: "4px 8px",
            background: "#fff",
          }}
        >
          {props.subscriptions.length === 0 ? (
            <div style={{ fontSize: 13, color: "#605e5c", padding: "8px 0" }}>
              No subscriptions available
            </div>
          ) : (
            props.subscriptions.map(sub => (
              <label
                key={sub.id}
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 8,
                  padding: "6px 4px",
                  cursor: "pointer",
                  fontSize: 14,
                  color: "#323130",
                  borderRadius: 2,
                }}
                onMouseEnter={e => e.currentTarget.style.background = "#f3f2f1"}
                onMouseLeave={e => e.currentTarget.style.background = "transparent"}
              >
                <input
                  type="checkbox"
                  checked={props.selectedSubscriptions.has(sub.id)}
                  onChange={e => {
                    const next = new Set(props.selectedSubscriptions);
                    if (e.target.checked) next.add(sub.id);
                    else next.delete(sub.id);
                    props.onSelectedSubscriptionsChange(next);
                  }}
                  style={{ marginTop: 3, flexShrink: 0, cursor: "pointer" }}
                />
                <div style={{ flex: 1, wordBreak: "break-word" }}>
                  <div style={{ fontWeight: 400 }}>{sub.name}</div>
                  <div style={{ fontSize: 12, color: "#605e5c", marginTop: 2 }}>{sub.id}</div>
                </div>
              </label>
            ))
          )}
        </div>
      </div>

      {hasSelection && (
        <>
          {/* View Level */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <h3 style={{ 
                margin: 0, 
                fontSize: 13, 
                fontWeight: 600, 
                color: "#323130",
                textTransform: "uppercase",
                letterSpacing: "0.5px"
              }}>
                View Level
              </h3>
              <span style={{ fontSize: 12, color: "#605e5c", fontWeight: 600 }}>
                {props.viewLevel === "overview" ? "L1" : props.viewLevel === "network" ? "L2" : "L3"}
              </span>
            </div>
            
            <input
              type="range"
              min="0"
              max="2"
              value={props.viewLevel === "overview" ? 0 : props.viewLevel === "network" ? 1 : 2}
              onChange={e => {
                const levels: Array<"overview" | "network" | "full"> = ["overview", "network", "full"];
                props.onViewLevelChange(levels[parseInt(e.target.value)]);
              }}
              style={{
                width: "100%",
                cursor: "pointer",
                height: 4,
                outline: "none",
                background: "#e0e0e0",
                borderRadius: 2,
              }}
            />
            
            <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontSize: 12, color: "#605e5c" }}>
              <span>Overview</span>
              <span></span>
              <span>Full</span>
            </div>
          </div>

          {showResourceGroupFilter && (
            <div style={{ marginBottom: 20 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: "#323130", textTransform: "uppercase", letterSpacing: "0.5px" }}>Resource Groups</h3>
                <div style={{ display: "flex", gap: 4 }}>
                  <button
                    onClick={() => {
                      const allGroups = props.resourceGroupOptions.map(rg => rg.key);
                      props.onResourceGroupFilterChange(new Set(allGroups));
                    }}
                    title="Select All"
                    style={{
                      background: "transparent",
                      border: "none",
                      color: "#0078d4",
                      cursor: "pointer",
                      padding: "2px 4px",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                    onMouseEnter={e => e.currentTarget.style.color = "#005a9e"}
                    onMouseLeave={e => e.currentTarget.style.color = "#0078d4"}
                  >
                    <CheckmarkRegular style={{ fontSize: 16 }} />
                  </button>
                  <button
                    onClick={() => {
                      props.onResourceGroupFilterChange(new Set());
                    }}
                    title="Uncheck All"
                    style={{
                      background: "transparent",
                      border: "none",
                      color: "#a4262c",
                      cursor: "pointer",
                      padding: "2px 4px",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                    onMouseEnter={e => e.currentTarget.style.color = "#750b1c"}
                    onMouseLeave={e => e.currentTarget.style.color = "#a4262c"}
                  >
                    <DismissRegular style={{ fontSize: 16 }} />
                  </button>
                </div>
              </div>
              <div
                style={{
                  maxHeight: 200,
                  overflowY: "auto",
                  border: "1px solid #8a8886",
                  borderRadius: 2,
                  padding: "4px 8px",
                  background: "#fff",
                }}
              >
                {props.resourceGroupOptions.map(rg => (
                  <label
                    key={rg.key}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      padding: "6px 4px",
                      cursor: "pointer",
                      fontSize: 14,
                      color: "#323130",
                      borderRadius: 2,
                    }}
                    onMouseEnter={e => e.currentTarget.style.background = "#f3f2f1"}
                    onMouseLeave={e => e.currentTarget.style.background = "transparent"}
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
                      style={{ cursor: "pointer" }}
                    />
                    {rg.label}
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Services */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: "#323130", textTransform: "uppercase", letterSpacing: "0.5px" }}>Services</h3>
              <div style={{ display: "flex", gap: 4 }}>
                <button
                  onClick={() => {
                    const allServices = props.serviceOptions.flatMap(cat => cat.services.map(s => s.key));
                    props.onServiceFilterChange(new Set(allServices));
                  }}
                  title="Select All"
                  style={{
                    background: "transparent",
                    border: "none",
                    color: "#0078d4",
                    cursor: "pointer",
                    padding: "2px 4px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                  onMouseEnter={e => e.currentTarget.style.color = "#005a9e"}
                  onMouseLeave={e => e.currentTarget.style.color = "#0078d4"}
                >
                  <CheckmarkRegular style={{ fontSize: 16 }} />
                </button>
                <button
                  onClick={() => {
                    props.onServiceFilterChange(new Set());
                  }}
                  title="Uncheck All"
                  style={{
                    background: "transparent",
                    border: "none",
                    color: "#a4262c",
                    cursor: "pointer",
                    padding: "2px 4px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                  onMouseEnter={e => e.currentTarget.style.color = "#750b1c"}
                  onMouseLeave={e => e.currentTarget.style.color = "#a4262c"}
                >
                  <DismissRegular style={{ fontSize: 16 }} />
                </button>
              </div>
            </div>
            <div
              style={{
                maxHeight: 400,
                overflowY: "auto",
                border: "1px solid #8a8886",
                borderRadius: 2,
                padding: "4px 8px",
                background: "#fff",
              }}
            >
              {props.serviceOptions.map(category => {
                const allServicesInCategory = category.services.map(s => s.key);
                const selectedServicesInCategory = allServicesInCategory.filter(key => props.serviceFilter.has(key));
                const isExpanded = props.expandedCategories.has(category.category);
                const allSelected = selectedServicesInCategory.length === allServicesInCategory.length;
                const someSelected = selectedServicesInCategory.length > 0 && !allSelected;

                return (
                  <div key={category.category} style={{ marginBottom: 2 }}>
                    <div 
                      style={{ 
                        display: "flex", 
                        alignItems: "center", 
                        gap: 8, 
                        padding: "6px 8px",
                        borderRadius: 2,
                        cursor: "pointer",
                      }}
                      onClick={() => {
                        const next = new Set(props.expandedCategories);
                        if (isExpanded) next.delete(category.category);
                        else next.add(category.category);
                        props.onExpandedCategoriesChange(next);
                      }}
                      onMouseEnter={e => e.currentTarget.style.background = "#f3f2f1"}
                      onMouseLeave={e => e.currentTarget.style.background = "transparent"}
                    >
                      <span
                        style={{
                          color: "#605e5c",
                          fontSize: 10,
                          width: 12,
                          display: "inline-block",
                          textAlign: "center",
                        }}
                      >
                        {isExpanded ? "▼" : "▶"}
                      </span>

                      <input
                        type="checkbox"
                        checked={allSelected}
                        ref={el => {
                          if (el) el.indeterminate = someSelected;
                        }}
                        onChange={e => {
                          e.stopPropagation();
                          const newFilter = new Set(props.serviceFilter);
                          if (e.target.checked) allServicesInCategory.forEach(key => newFilter.add(key));
                          else allServicesInCategory.forEach(key => newFilter.delete(key));
                          props.onServiceFilterChange(newFilter);
                        }}
                        onClick={e => e.stopPropagation()}
                        style={{ cursor: "pointer", margin: 0 }}
                      />

                      <span style={{ fontSize: 14, fontWeight: 600, color: "#323130", flex: 1 }}>
                        {category.category}
                      </span>
                      
                      <span style={{ fontSize: 12, color: "#605e5c" }}>
                        {category.services.length}
                      </span>
                    </div>

                    {isExpanded && (
                      <div style={{ paddingLeft: 20 }}>
                        {category.services.map(service => (
                          <label
                            key={service.key}
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: 8,
                              padding: "6px 8px",
                              cursor: "pointer",
                              fontSize: 14,
                              color: "#323130",
                              borderRadius: 2,
                            }}
                            onMouseEnter={e => e.currentTarget.style.background = "#f3f2f1"}
                            onMouseLeave={e => e.currentTarget.style.background = "transparent"}
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
                              style={{ cursor: "pointer", margin: 0 }}
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
              ...secondaryButton,
              width: "100%",
              background: props.showLegend ? "rgba(0, 120, 212, 0.1)" : "transparent",
              borderColor: props.showLegend ? "#0078d4" : "#8a8886",
            }}
            onMouseEnter={e => {
              if (!props.showLegend) {
                e.currentTarget.style.background = "rgba(0, 120, 212, 0.05)";
                e.currentTarget.style.borderColor = "#0078d4";
              }
            }}
            onMouseLeave={e => {
              if (!props.showLegend) {
                e.currentTarget.style.background = "transparent";
                e.currentTarget.style.borderColor = "#8a8886";
              }
            }}
          >
            {props.showLegend ? "Hide" : "Show"} Legend
          </button>
        </>
      )}
    </div>
  );
};

export default WorkloadSidebar;
