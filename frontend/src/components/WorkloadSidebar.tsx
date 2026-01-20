import React from "react";
import type { ViewLevel } from "../domain/graphView";

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
  const hasSelection = props.selectedSubscriptions.size > 0;

  return (
    <div
      style={{
        padding: 16,
        overflowY: "auto",
        overflowX: "hidden",
      }}
    >
      <h3 style={{ margin: "0 0 20px 0", color: "#eee", fontSize: 16 }}>Controls</h3>

      {/* Workload Views */}
      <div style={{ marginBottom: 20 }}>
        <label style={{ display: "block", fontSize: 12, color: "#9AA0A6", marginBottom: 8 }}>
          Workload
        </label>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <select
            value={props.activeWorkloadId ?? ""}
            onChange={e => props.onWorkloadSelect(e.target.value || null)}
            style={{
              width: "100%",
              background: "#181818",
              color: "#fff",
              border: "1px solid #333",
              padding: "8px",
              borderRadius: 4,
            }}
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
              background: "#181818",
              color: "#fff",
              border: "1px solid #333",
              padding: "8px",
              borderRadius: 4,
            }}
          />

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <button
              onClick={props.onWorkloadCreate}
              disabled={!props.workloadName.trim()}
              style={{
                padding: "8px",
                background: props.workloadName.trim()
                  ? (!props.activeWorkloadId && props.workloadNewDirty ? "#2563eb" : "#1f3a5f")
                  : "#222",
                color: props.workloadName.trim() ? "#fff" : "#666",
                border: !props.activeWorkloadId && props.workloadNewDirty ? "1px solid #60a5fa" : "1px solid #333",
                borderRadius: 4,
                cursor: props.workloadName.trim() ? "pointer" : "not-allowed",
                fontSize: 12,
                boxShadow: !props.activeWorkloadId && props.workloadNewDirty ? "0 0 0 1px rgba(96,165,250,0.4)" : "none",
              }}
            >
              Save New
            </button>
            <button
              onClick={props.onWorkloadSave}
              disabled={!props.activeWorkloadId}
              style={{
                padding: "8px",
                background: props.activeWorkloadId
                  ? (props.workloadDirty ? "#16a34a" : "#0f3d2e")
                  : "#222",
                color: props.activeWorkloadId ? "#fff" : "#666",
                border: props.workloadDirty ? "1px solid #4ade80" : "1px solid #333",
                borderRadius: 4,
                cursor: props.activeWorkloadId ? "pointer" : "not-allowed",
                fontSize: 12,
                boxShadow: props.workloadDirty ? "0 0 0 1px rgba(74,222,128,0.35)" : "none",
              }}
            >
              Save Changes
            </button>
            <button
              onClick={props.onWorkloadRename}
              disabled={!props.activeWorkloadId || !props.workloadName.trim()}
              style={{
                padding: "8px",
                background: props.activeWorkloadId && props.workloadName.trim() ? "#3b2b1a" : "#222",
                color: props.activeWorkloadId && props.workloadName.trim() ? "#fff" : "#666",
                border: "1px solid #333",
                borderRadius: 4,
                cursor: props.activeWorkloadId && props.workloadName.trim() ? "pointer" : "not-allowed",
                fontSize: 12,
              }}
            >
              Rename
            </button>
            <button
              onClick={props.onWorkloadDelete}
              disabled={!props.activeWorkloadId}
              style={{
                padding: "8px",
                background: props.activeWorkloadId ? "#4a1c1c" : "#222",
                color: props.activeWorkloadId ? "#fff" : "#666",
                border: "1px solid #333",
                borderRadius: 4,
                cursor: props.activeWorkloadId ? "pointer" : "not-allowed",
                fontSize: 12,
              }}
            >
              Delete
            </button>
          </div>

          {props.workloadError && (
            <div style={{ fontSize: 11, color: "#f87171" }}>{props.workloadError}</div>
          )}
        </div>
      </div>

      {/* Subscription Selector */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <label style={{ fontSize: 12, color: "#9AA0A6" }}>Subscriptions</label>
          <button
            onClick={() => {
              const allSubscriptions = props.subscriptions.map(s => s.id);
              props.onSelectedSubscriptionsChange(new Set(allSubscriptions));
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
            maxHeight: 180,
            overflowY: "auto",
            border: "1px solid #333",
            borderRadius: 4,
            padding: 8,
            background: "#181818",
          }}
        >
          {props.subscriptions.length === 0 ? (
            <div style={{ fontSize: 12, color: "#666", padding: "8px 0" }}>
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
                  padding: "6px 0",
                  cursor: "pointer",
                  fontSize: 12,
                  color: "#ddd",
                }}
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
                  style={{ marginTop: 2, flexShrink: 0 }}
                />
                <div style={{ flex: 1, wordBreak: "break-word" }}>
                  <div style={{ fontWeight: 500 }}>{sub.name}</div>
                  <div style={{ fontSize: 11, color: "#666" }}>{sub.id}</div>
                </div>
              </label>
            ))
          )}
        </div>
      </div>

      {hasSelection && (
        <>
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
              <option value="overview">Overview (L1)</option>
              <option value="network">Network (L2)</option>
              <option value="full">Full (L3)</option>
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
        </>
      )}
    </div>
  );
};

export default WorkloadSidebar;
