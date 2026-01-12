import React, { useState, useMemo } from "react";
import { canonicalTypeForNode, normalizeTypeString } from "../domain/graphView";

interface ResilienceCheck {
  status: "pass" | "fail";
  description: string;
  category: string;
  impact: "High" | "Medium" | "Low";
  criticality_weight: number;
  recommendation_id: string;
  long_description?: string;
  potential_benefits?: string;
  learn_more?: Array<{ name: string; url: string }>;
}

interface ResilienceEvaluation {
  resource_id: string;
  resource_type: string;
  resource_name: string;
  total_checks: number;
  passed_checks: number;
  failed_checks: number;
  overall_score: number;
  checks: ResilienceCheck[];
  findings: ResilienceCheck[];
  scores: Record<string, number>;
}

interface LLMAnnotation {
  display_name?: string;
  azure_service_category?: string;
  azure_service_name?: string;
}

interface ResilienceSummaryProps {
  evaluations: Record<string, ResilienceEvaluation>;
  graphData?: {
    nodes?: Array<{ id: string; name?: string; type?: string; metadata?: Record<string, unknown> }>;
    llm_annotations?: {
      nodes?: Array<{
        node_id: string;
        annotations: LLMAnnotation;
      }>;
    };
  };
  viewLevel?: ViewLevel;
  resourceGroupFilter?: Set<string>;
  serviceFilter?: Set<string>;
}

type ViewLevel = "overview" | "network" | "full";

// Reusable donut chart component
const DonutChart: React.FC<{
  passed: number;
  failed: number;
  size?: number;
}> = ({ passed, failed, size = 140 }) => {
  const total = passed + failed;
  if (total === 0) {
    return (
      <div
        style={{
          width: `${size}px`,
          height: `${size}px`,
          borderRadius: "50%",
          background: "#e5e7eb",
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
        }}
      >
        <span style={{ fontSize: "12px", color: "#6b7280", fontWeight: 600 }}>N/A</span>
      </div>
    );
  }

  const passPercentage = (passed / total) * 100;
  const conicGradient = `conic-gradient(
    from 0deg,
    #10b981 0deg ${(passPercentage / 100) * 360}deg,
    #ef4444 ${(passPercentage / 100) * 360}deg 360deg
  )`;

  return (
    <div
      style={{
        position: "relative",
        width: `${size}px`,
        height: `${size}px`,
        borderRadius: "50%",
        background: conicGradient,
        padding: "4px",
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      <div
        style={{
          width: "100%",
          height: "100%",
          borderRadius: "50%",
          background: "#ffffff",
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          flexDirection: "column",
          position: "relative",
        }}
      >
        <span
          style={{
            fontSize: "20px",
            fontWeight: 700,
            color: "#1f2937",
          }}
        >
          {passPercentage.toFixed(0)}%
        </span>
        <span style={{ fontSize: "10px", color: "#6b7280" }}>Pass</span>
      </div>
    </div>
  );
};

type SortColumn = "resource" | "recommendation" | "category" | "impact" | "status" | "benefit";

const ResilienceSummary: React.FC<ResilienceSummaryProps> = ({
  evaluations,
  graphData,
  viewLevel,
  resourceGroupFilter,
  serviceFilter,
}) => {
  const [expandedResource, setExpandedResource] = useState<string | null>(null);
  const [filterStatus, setFilterStatus] = useState<"all" | "pass" | "fail">("fail");
  const [breakdownView, setBreakdownView] = useState<"category" | "impact" | "service">("category");
  const [sortColumn, setSortColumn] = useState<SortColumn>("impact");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("desc");
  const [filterCategory, setFilterCategory] = useState<string | null>(null);
  const [filterImpact, setFilterImpact] = useState<string | null>(null);
  const [resourceFilter, setResourceFilter] = useState("");

  // Create annotation lookup
  const annotationMap = useMemo(() => {
    const map = new Map<string, LLMAnnotation>();
    if (graphData?.llm_annotations?.nodes) {
      graphData.llm_annotations.nodes.forEach(node => {
        map.set(node.node_id, node.annotations);
      });
    }
    return map;
  }, [graphData]);

  // Map resourceId -> canonical type key (must match WorkloadSidebar service filter semantics)
  const resourceIdToServiceKey = useMemo(() => {
    const map = new Map<string, string>();
    const nodes = graphData?.nodes ?? [];
    for (const n of nodes) {
      const id = String(n?.id ?? "").toLowerCase();
      if (!id) continue;
      map.set(id, canonicalTypeForNode(n as any));
    }
    return map;
  }, [graphData?.nodes]);

  // Initialize filters with all available options if not provided
  const allResourceGroups = useMemo(() => {
    const groups = new Set<string>();
    Object.keys(evaluations).forEach(resourceId => {
      const parts = resourceId.split("/").filter(p => p);
      const partsLower = parts.map(p => p.toLowerCase());
      const rgIndex = partsLower.indexOf("resourcegroups");
      if (rgIndex !== -1 && rgIndex + 1 < parts.length) {
        groups.add(String(parts[rgIndex + 1]).toLowerCase());
      }
    });
    return groups;
  }, [evaluations]);

  // WorkloadSidebar's service filter stores canonical node type keys (see buildViewGraph typeAllowed).
  const allServiceKeys = useMemo(() => {
    const keys = new Set<string>();
    if (resourceIdToServiceKey.size > 0) {
      for (const v of resourceIdToServiceKey.values()) {
        const key = String(v ?? "").toLowerCase();
        if (key) keys.add(key);
      }
      return keys;
    }
    // Fallback: derive from evaluation.resource_type when graph nodes aren't available.
    Object.values(evaluations).forEach((evaluation: any) => {
      const key = normalizeTypeString(evaluation?.resource_type);
      if (key) keys.add(String(key).toLowerCase());
    });
    return keys;
  }, [evaluations, resourceIdToServiceKey]);

  // Default to all filters selected if not provided or empty
  const effectiveResourceGroupFilter = useMemo(() => {
    if (!resourceGroupFilter || resourceGroupFilter.size === 0) {
      return allResourceGroups.size > 0 ? allResourceGroups : new Set<string>();
    }
    return new Set(Array.from(resourceGroupFilter).map(v => String(v).toLowerCase()));
  }, [resourceGroupFilter, allResourceGroups]);

  const effectiveServiceFilter = useMemo(() => {
    if (!serviceFilter || serviceFilter.size === 0) {
      return allServiceKeys.size > 0 ? allServiceKeys : new Set<string>();
    }
    return new Set(Array.from(serviceFilter).map(v => String(v).toLowerCase()));
  }, [serviceFilter, allServiceKeys]);

  const resourceMatchesFilters = (resourceId: string, evaluation?: any): boolean => {
    // Check resource group filter
    if (effectiveResourceGroupFilter.size > 0) {
      // Extract resource group from Azure resource ID
      // Format: /subscriptions/sub-id/resourceGroups/group-name/providers/...
      const parts = resourceId.split("/").filter(p => p);
      const partsLower = parts.map(p => p.toLowerCase());
      const rgIndex = partsLower.indexOf("resourcegroups");
      const resourceGroup =
        rgIndex !== -1 && rgIndex + 1 < parts.length
          ? String(parts[rgIndex + 1]).toLowerCase()
          : null;

      if (resourceGroup && !effectiveResourceGroupFilter.has(resourceGroup)) {
        return false;
      }
    }

    // Check service filter
    if (effectiveServiceFilter.size > 0) {
      const keyFromGraph = resourceIdToServiceKey.get(resourceId.toLowerCase());
      const keyFromEval = normalizeTypeString(evaluation?.resource_type);
      const serviceKey = String((keyFromGraph ?? keyFromEval ?? "") as string).toLowerCase();
      if (serviceKey && !effectiveServiceFilter.has(serviceKey)) return false;
    }

    return true;
  };

  const filteredEvaluationEntries = useMemo(() => {
    return Object.entries(evaluations).filter(([resourceId, evaluation]) =>
      resourceMatchesFilters(resourceId, evaluation)
    );
  }, [evaluations, effectiveResourceGroupFilter, effectiveServiceFilter, resourceIdToServiceKey]);

  const getResourceDisplayName = (resourceId: string, defaultName: string): string => {
    // Check if this is a subscription-level resource (/subscriptions/{id})
    const parts = resourceId.split("/").filter(p => p);
    if (parts.length === 2 && parts[0] === "subscriptions") {
      return "Subscription level";
    }
    return defaultName;
  };

  const getImpactColor = (impact: string) => {
    switch (impact) {
      case "High":
        return "#dc2626";
      case "Medium":
        return "#f59e0b";
      case "Low":
        return "#10b981";
      default:
        return "#6b7280";
    }
  };

  const stats = useMemo(() => {
    let totalChecks = 0;
    let passedChecks = 0;
    let failedChecks = 0;
    const resourceList: Array<ResilienceEvaluation & { resourceId: string }> = [];

    filteredEvaluationEntries.forEach(([resourceId, evaluation]) => {
      totalChecks += evaluation.total_checks;
      passedChecks += evaluation.passed_checks;
      failedChecks += evaluation.failed_checks;
      resourceList.push({ ...evaluation, resourceId });
    });

    const passPercentage = totalChecks > 0 ? (passedChecks / totalChecks) * 100 : 0;

    return { totalChecks, passedChecks, failedChecks, passPercentage, resourceList };
  }, [filteredEvaluationEntries]);

  const resourceFilterOptions = useMemo(() => {
    const seen = new Set<string>();
    stats.resourceList.forEach(resource => {
      const annotationName = annotationMap.get(resource.resourceId)?.display_name || resource.resource_name || "";
      const displayName = getResourceDisplayName(resource.resourceId, annotationName);
      if (displayName) seen.add(displayName);
    });
    return Array.from(seen).sort((a, b) => a.localeCompare(b));
  }, [stats.resourceList, annotationMap]);

  // Breakdown by resilience category (HighAvailability, Scalability, etc.)
  const categoryBreakdown = useMemo(() => {
    const breakdown = new Map<string, { total: number; passed: number; failed: number }>();
    
    filteredEvaluationEntries.forEach(([, evaluation]) => {
      const checksOrFindings = evaluation.findings || evaluation.checks || [];
      checksOrFindings.forEach((finding: any) => {
        const category = finding.category || "Other";
        const existing = breakdown.get(category) || { total: 0, passed: 0, failed: 0 };
        existing.total++;
        if (finding.status === "pass") existing.passed++;
        else existing.failed++;
        breakdown.set(category, existing);
      });
    });

    return Array.from(breakdown.entries())
      .map(([name, stats]) => ({
        name,
        ...stats,
        passPercentage: stats.total > 0 ? (stats.passed / stats.total) * 100 : 0,
      }))
      .sort((a, b) => b.failed - a.failed);
  }, [filteredEvaluationEntries]);

  // Breakdown by impact level
  const impactBreakdown = useMemo(() => {
    const breakdown = new Map<string, { total: number; passed: number; failed: number }>();
    
    filteredEvaluationEntries.forEach(([, evaluation]) => {
      const checksOrFindings = evaluation.findings || evaluation.checks || [];
      checksOrFindings.forEach((finding: any) => {
        const impact = finding.impact || "Unknown";
        const existing = breakdown.get(impact) || { total: 0, passed: 0, failed: 0 };
        existing.total++;
        if (finding.status === "pass") existing.passed++;
        else existing.failed++;
        breakdown.set(impact, existing);
      });
    });

    const order = ["High", "Medium", "Low", "Unknown"];
    return Array.from(breakdown.entries())
      .map(([name, stats]) => ({
        name,
        ...stats,
        passPercentage: stats.total > 0 ? (stats.passed / stats.total) * 100 : 0,
      }))
      .sort((a, b) => order.indexOf(a.name) - order.indexOf(b.name));
  }, [filteredEvaluationEntries]);

  // Failed counts for impact levels (High/Medium/Low) for tooltip and donut
  const failedImpactCounts = useMemo(() => {
    const getFailed = (name: string) => impactBreakdown.find(i => i.name === name)?.failed || 0;
    const high = getFailed("High");
    const medium = getFailed("Medium");
    const low = getFailed("Low");
    const total = high + medium + low;
    return { high, medium, low, total };
  }, [impactBreakdown]);

  // Breakdown by Azure service category
  const serviceBreakdown = useMemo(() => {
    const breakdown = new Map<string, { total: number; passed: number; failed: number }>();
    
    filteredEvaluationEntries.forEach(([resourceId, evaluation]: [string, any]) => {
      const annotation = annotationMap.get(resourceId);
      const serviceCategory = annotation?.azure_service_category || "Other";
      
      const checksOrFindings = evaluation.findings || evaluation.checks || [];
      checksOrFindings.forEach((finding: any) => {
        const existing = breakdown.get(serviceCategory) || { total: 0, passed: 0, failed: 0 };
        existing.total++;
        if (finding.status === "pass") existing.passed++;
        else existing.failed++;
        breakdown.set(serviceCategory, existing);
      });
    });

    return Array.from(breakdown.entries())
      .map(([name, stats]) => ({
        name,
        ...stats,
        passPercentage: stats.total > 0 ? (stats.passed / stats.total) * 100 : 0,
      }))
      .sort((a, b) => b.failed - a.failed);
  }, [filteredEvaluationEntries, annotationMap]);

  const filteredFindings = useMemo(() => {
    const normalizedResourceFilter = resourceFilter.trim().toLowerCase();
    let findings = filteredEvaluationEntries.flatMap(([resourceId, evaluation]) => {
      // Use findings if available, otherwise fall back to checks
      const checksOrFindings = (evaluation as any).findings || (evaluation as any).checks || [];
      return checksOrFindings
        .filter((f: any) => filterStatus === "all" || f.status === filterStatus)
        .filter((f: any) => !filterCategory || f.category === filterCategory)
        .filter((f: any) => !filterImpact || f.impact === filterImpact)
        .map((finding: any) => ({
          ...finding,
          resourceId,
          resourceName: evaluation.resource_name,
        }));
    });

    if (normalizedResourceFilter) {
      findings = findings.filter(finding => {
        const annotationName = annotationMap.get(finding.resourceId)?.display_name || finding.resourceName || "";
        const resourceLabel = getResourceDisplayName(finding.resourceId, annotationName);
        return resourceLabel.toLowerCase().includes(normalizedResourceFilter);
      });
    }

    // Sorting
    findings.sort((a, b) => {
      let aVal: any = "";
      let bVal: any = "";

      switch (sortColumn) {
        case "resource":
          aVal = annotationMap.get(a.resourceId)?.display_name || a.resourceName;
          bVal = annotationMap.get(b.resourceId)?.display_name || b.resourceName;
          break;
        case "recommendation":
          aVal = a.description;
          bVal = b.description;
          break;
        case "category":
          aVal = a.category;
          bVal = b.category;
          break;
        case "impact":
          const impactOrder = { "High": 3, "Medium": 2, "Low": 1 };
          aVal = impactOrder[a.impact as keyof typeof impactOrder] || 0;
          bVal = impactOrder[b.impact as keyof typeof impactOrder] || 0;
          break;
        case "status":
          aVal = a.status === "pass" ? 1 : 0;
          bVal = b.status === "pass" ? 1 : 0;
          break;
        case "benefit":
          aVal = a.potential_benefits || "";
          bVal = b.potential_benefits || "";
          break;
      }

      if (typeof aVal === "string") {
        aVal = aVal.toLowerCase();
        bVal = bVal.toLowerCase();
        return sortDirection === "asc" ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
      }
      return sortDirection === "asc" ? aVal - bVal : bVal - aVal;
    });

    return findings;
  }, [filteredEvaluationEntries, filterStatus, filterCategory, filterImpact, resourceFilter, sortColumn, sortDirection, annotationMap]);

  const getStatusColor = (status: string) => {
    return status === "pass" ? "#10b981" : "#ef4444";
  };

  const getRiskLevel = (passPercentage: number) => {
    if (passPercentage >= 80) return { level: "Low Risk", color: "#10b981", badge: "✓" };
    if (passPercentage >= 60) return { level: "Medium Risk", color: "#f97316", badge: "!" };
    if (passPercentage >= 40) return { level: "High Risk", color: "#ef4444", badge: "⚠" };
    return { level: "Critical", color: "#7c2d12", badge: "🔴" };
  };

  const riskLevel = getRiskLevel(stats.passPercentage);

  const toggleSort = (column: SortColumn) => {
    if (sortColumn === column) {
      setSortDirection(prev => (prev === "asc" ? "desc" : "asc"));
    } else {
      setSortColumn(column);
      setSortDirection("desc");
    }
  };

  const sortIndicator = (column: SortColumn) => {
    if (sortColumn !== column) return "";
    return sortDirection === "asc" ? "▲" : "▼";
  };

  return (
    <div style={{ padding: "24px", fontFamily: "Segoe UI, system-ui, sans-serif", background: "#f9fafb", minHeight: "100vh" }}>
      {/* Header Section - Overview Card */}
      <div
        style={{
          background: "#fff",
          borderRadius: "12px",
          padding: "24px",
          marginBottom: "24px",
          boxShadow: "0 1px 3px rgba(0,0,0,0.1)",
          border: "1px solid #e5e7eb",
        }}
      >
        <h1
          style={{
            margin: "0 0 24px",
            fontSize: "24px",
            fontWeight: 700,
            color: "#1f2937",
          }}
        >
          Resilience Overview
        </h1>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "auto auto 1fr auto",
            gap: "24px",
            alignItems: "flex-start",
          }}
        >
          {/* Pass/Fail Donut Chart */}
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "12px" }}>
            <DonutChart passed={stats.passedChecks} failed={stats.failedChecks} size={140} />
            <div style={{ textAlign: "center" }}>
              <div style={{ fontSize: "12px", fontWeight: 600, color: riskLevel.color }}>
                {riskLevel.level}
              </div>
            </div>
          </div>

          {/* Impact Donut Chart - All three levels */}
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "12px" }}>
            <div
              title={`Failed items: ${failedImpactCounts.total} (High: ${failedImpactCounts.high}, Medium: ${failedImpactCounts.medium}, Low: ${failedImpactCounts.low})`}
              style={{
                position: "relative",
                width: "140px",
                height: "140px",
                borderRadius: "50%",
                background: (() => {
                  const { high, medium, low, total } = failedImpactCounts;
                  if (total === 0) return "#e5e7eb";
                  const highPct = (high / total) * 100;
                  const mediumPct = (medium / total) * 100;
                  const lowPct = (low / total) * 100;
                  return `conic-gradient(
                    from 0deg,
                    #dc2626 0deg ${(highPct / 100) * 360}deg,
                    #f59e0b ${(highPct / 100) * 360}deg ${((highPct + mediumPct) / 100) * 360}deg,
                    #10b981 ${((highPct + mediumPct) / 100) * 360}deg 360deg
                  )`;
                })(),
                padding: "4px",
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
              }}
            >
              <div
                style={{
                  width: "100%",
                  height: "100%",
                  borderRadius: "50%",
                  background: "#ffffff",
                  display: "flex",
                  justifyContent: "center",
                  alignItems: "center",
                  flexDirection: "column",
                }}
              >
                <span style={{ fontSize: "18px", fontWeight: 700, color: "#1f2937" }}>
                  {failedImpactCounts.total}
                </span>
                <span style={{ fontSize: "10px", color: "#6b7280" }}>Failed</span>
              </div>
            </div>
            <div style={{ textAlign: "center", fontSize: "11px", fontWeight: 600, color: "#1f2937" }}>
              Impact
            </div>
          </div>

          {/* Main Stats + Category + Impact bars */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "12px", marginBottom: "16px" }}>
                <div
                style={{
                  padding: "10px",
                  background: "#dee7f3",
                  borderRadius: "8px",
                  borderLeft: "3px solid #448eef",
                }}
              >
                <div style={{ fontSize: "11px", color: "#5a606b", marginBottom: "4px" }}>Total Checks</div>
                <div style={{ fontSize: "18px", fontWeight: 700, color: "#448eef" }}>
                  {stats.totalChecks}
                </div>
              </div>

              <div
                style={{
                  padding: "10px",
                  background: "#ecfdf5",
                  borderRadius: "8px",
                  borderLeft: "3px solid #10b981",
                }}
              >
                <div style={{ fontSize: "11px", color: "#6b7280", marginBottom: "4px" }}>Passed</div>
                <div style={{ fontSize: "18px", fontWeight: 700, color: "#10b981" }}>
                  {stats.passedChecks}
                </div>
              </div>
              <div
                style={{
                  padding: "10px",
                  background: "#fee2e2",
                  borderRadius: "8px",
                  borderLeft: "3px solid #ef4444",
                }}
              >
                <div style={{ fontSize: "11px", color: "#6b7280", marginBottom: "4px" }}>Failed</div>
                <div style={{ fontSize: "18px", fontWeight: 700, color: "#ef4444" }}>
                  {stats.failedChecks}
                </div>
              </div>
          </div>
        </div>
      </div>

      {/* Resources Grid */}
      <div style={{ marginBottom: "24px" }}>
        {/* Internal Tabs */}
        <div
          style={{
            display: "flex",
            borderBottom: "2px solid #e5e7eb",
            marginBottom: "16px",
            gap: "0",
          }}
        >
          <button
            onClick={() => setBreakdownView("category")}
            style={{
              padding: "12px 20px",
              border: "none",
              background: breakdownView === "category" ? "#fff" : "transparent",
              color: breakdownView === "category" ? "#0078d4" : "#6b7280",
              cursor: "pointer",
              fontSize: "13px",
              fontWeight: breakdownView === "category" ? 600 : 500,
              borderBottom: breakdownView === "category" ? "3px solid #0078d4" : "none",
              marginBottom: "-2px",
              transition: "all 0.2s ease",
            }}
          >
            By Resilience Category
          </button>
          <button
            onClick={() => setBreakdownView("impact")}
            style={{
              padding: "12px 20px",
              border: "none",
              background: breakdownView === "impact" ? "#fff" : "transparent",
              color: breakdownView === "impact" ? "#0078d4" : "#6b7280",
              cursor: "pointer",
              fontSize: "13px",
              fontWeight: breakdownView === "impact" ? 600 : 500,
              borderBottom: breakdownView === "impact" ? "3px solid #0078d4" : "none",
              marginBottom: "-2px",
              transition: "all 0.2s ease",
            }}
          >
            By Impact Level
          </button>
          <button
            onClick={() => setBreakdownView("service")}
            style={{
              padding: "12px 20px",
              border: "none",
              background: breakdownView === "service" ? "#fff" : "transparent",
              color: breakdownView === "service" ? "#0078d4" : "#6b7280",
              cursor: "pointer",
              fontSize: "13px",
              fontWeight: breakdownView === "service" ? 600 : 500,
              borderBottom: breakdownView === "service" ? "3px solid #0078d4" : "none",
              marginBottom: "-2px",
              transition: "all 0.2s ease",
            }}
          >
            By Azure Service
          </button>
        </div>

        {/* Breakdown Grid */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))",
            gap: "8px",
          }}
        >
          {breakdownView === "category" &&
            categoryBreakdown.map((item) => (
              <div
                key={item.name}
                style={{
                  background: "#fff",
                  borderRadius: "6px",
                  padding: "12px",
                  boxShadow: "0 1px 2px rgba(0,0,0,0.08)",
                  border: "1px solid #e5e7eb",
                }}
              >
                <div style={{ fontSize: "12px", fontWeight: 600, color: "#1f2937", marginBottom: "10px" }}>
                  {item.name}
                </div>

                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: "8px",
                  }}
                >
                  <DonutChart passed={item.passed} failed={item.failed} size={60} />
                  <div style={{ textAlign: "right", flex: 1, marginLeft: "12px" }}>
                    <div style={{ fontSize: "10px", color: "#6b7280", marginBottom: "2px" }}>Checks</div>
                    <div style={{ fontSize: "18px", fontWeight: 700, color: "#1f2937" }}>
                      {item.total}
                    </div>
                  </div>
                </div>

                <div style={{ marginTop: "4px" }}>
                  <div
                    style={{
                      display: "flex",
                      height: "6px",
                      borderRadius: "999px",
                      overflow: "hidden",
                      background: "#e5e7eb",
                    }}
                  >
                    <div
                      style={{
                        width: `${item.total ? (item.passed / item.total) * 100 : 0}%`,
                        background: "#10b981",
                      }}
                    />
                    <div
                      style={{
                        width: `${item.total ? (item.failed / item.total) * 100 : 0}%`,
                        background: "#ef4444",
                      }}
                    />
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "10px", marginTop: "4px" }}>
                    <span style={{ color: "#10b981", fontWeight: 600 }}>{item.passed}</span>
                    <span style={{ color: "#ef4444", fontWeight: 600 }}>{item.failed}</span>
                  </div>
                </div>
              </div>
            ))}

          {breakdownView === "impact" &&
            impactBreakdown.map((item) => (
              <div
                key={item.name}
                style={{
                  background: "#fff",
                  borderRadius: "6px",
                  padding: "12px",
                  boxShadow: "0 1px 2px rgba(0,0,0,0.08)",
                  border: "1px solid #e5e7eb",
                }}
              >
                <div style={{ fontSize: "12px", fontWeight: 600, color: getImpactColor(item.name), marginBottom: "10px" }}>
                  {item.name} Impact
                </div>

                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: "8px",
                  }}
                >
                  <DonutChart passed={item.passed} failed={item.failed} size={60} />
                  <div style={{ textAlign: "right", flex: 1, marginLeft: "12px" }}>
                    <div style={{ fontSize: "10px", color: "#6b7280", marginBottom: "2px" }}>Checks</div>
                    <div style={{ fontSize: "18px", fontWeight: 700, color: "#1f2937" }}>
                      {item.total}
                    </div>
                  </div>
                </div>

                <div style={{ marginTop: "4px" }}>
                  <div
                    style={{
                      display: "flex",
                      height: "6px",
                      borderRadius: "999px",
                      overflow: "hidden",
                      background: "#e5e7eb",
                    }}
                  >
                    <div
                      style={{
                        width: `${item.total ? (item.passed / item.total) * 100 : 0}%`,
                        background: "#10b981",
                      }}
                    />
                    <div
                      style={{
                        width: `${item.total ? (item.failed / item.total) * 100 : 0}%`,
                        background: "#ef4444",
                      }}
                    />
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "10px", marginTop: "4px" }}>
                    <span style={{ color: "#10b981", fontWeight: 600 }}>{item.passed}</span>
                    <span style={{ color: "#ef4444", fontWeight: 600 }}>{item.failed}</span>
                  </div>
                </div>
              </div>
            ))}

          {breakdownView === "service" &&
            serviceBreakdown.map((item) => (
              <div
                key={item.name}
                style={{
                  background: "#fff",
                    borderRadius: "6px",
                  padding: "12px",
                  boxShadow: "0 1px 2px rgba(0,0,0,0.08)",
                  border: "1px solid #e5e7eb",
                }}
              >
                <div style={{ fontSize: "12px", fontWeight: 600, color: "#1f2937", marginBottom: "10px" }}>
                  {item.name}
                </div>

                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: "8px",
                  }}
                >
                  <DonutChart passed={item.passed} failed={item.failed} size={60} />
                  <div style={{ textAlign: "right", flex: 1, marginLeft: "12px" }}>
                    <div style={{ fontSize: "10px", color: "#6b7280", marginBottom: "2px" }}>Checks</div>
                    <div style={{ fontSize: "18px", fontWeight: 700, color: "#1f2937" }}>
                      {item.total}
                    </div>
                  </div>
                </div>

                <div style={{ marginTop: "4px" }}>
                  <div
                    style={{
                      display: "flex",
                      height: "6px",
                      borderRadius: "999px",
                      overflow: "hidden",
                      background: "#e5e7eb",
                    }}
                  >
                    <div
                      style={{
                        width: `${item.total ? (item.passed / item.total) * 100 : 0}%`,
                        background: "#10b981",
                      }}
                    />
                    <div
                      style={{
                        width: `${item.total ? (item.failed / item.total) * 100 : 0}%`,
                        background: "#ef4444",
                      }}
                    />
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "10px", marginTop: "4px" }}>
                    <span style={{ color: "#10b981", fontWeight: 600 }}>{item.passed}</span>
                    <span style={{ color: "#ef4444", fontWeight: 600 }}>{item.failed}</span>
                  </div>
                </div>
              </div>
            ))}
        </div>
      </div>

      {/* Filter and Findings Table */}
      <div
        style={{
          background: "#fff",
          borderRadius: "12px",
          padding: "10px",
          boxShadow: "0 1px 3px rgba(0,0,0,0.1)",
          border: "1px solid #e5e7eb",
        }}
      >
        {/* Filter Buttons */}
        <div style={{ marginBottom: "8px", display: "flex", flexWrap: "wrap", gap: "8px" }}>
          <label style={{ fontSize: "12px", fontWeight: 600, color: "#1f2937", display: "flex", flexDirection: "column", gap: "6px" }}>
            Findings Details
            <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>

          <button
            onClick={() => setFilterStatus("fail")}
            style={{
              padding: "6px 16px",
              borderRadius: "6px",
              border: filterStatus === "fail" ? "2px solid #ef4444" : "1px solid #d1d5db",
              background: filterStatus === "fail" ? "#fee2e2" : "#fff",
              color: filterStatus === "fail" ? "#ef4444" : "#6b7280",
              cursor: "pointer",
              fontSize: "12px",
              fontWeight: 600,
            }}
          >
            Failed
          </button>
          <button
            onClick={() => setFilterStatus("pass")}
            style={{
              padding: "6px 16px",
              borderRadius: "6px",
              border: filterStatus === "pass" ? "2px solid #10b981" : "1px solid #d1d5db",
              background: filterStatus === "pass" ? "#ecfdf5" : "#fff",
              color: filterStatus === "pass" ? "#10b981" : "#6b7280",
              cursor: "pointer",
              fontSize: "12px",
              fontWeight: 600,
            }}
          >
            Passed
          </button>
          <button
            onClick={() => setFilterStatus("all")}
            style={{
              padding: "6px 16px",
              borderRadius: "6px",
              border: filterStatus === "all" ? "2px solid #0078d4" : "1px solid #d1d5db",
              background: filterStatus === "all" ? "#e0f2fe" : "#fff",
              color: filterStatus === "all" ? "#0078d4" : "#6b7280",
              cursor: "pointer",
              fontSize: "12px",
              fontWeight: 600,
            }}
          >
            All
          </button>
          </div>
        </label>

          <label style={{ fontSize: "12px", fontWeight: 600, color: "#1f2937", minWidth: "220px", display: "flex", flexDirection: "column", gap: "8px", marginLeft: "10px" }}>
            Resource
            <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
              <input
                list="resource-filter-options"
                value={resourceFilter}
                onChange={(e) => setResourceFilter(e.target.value)}
                placeholder="Type to search resources"
                style={{
                  flex: 1,
                  padding: "6px 8px",
                  borderRadius: "4px",
                  border: "1px solid #d1d5db",
                  fontSize: "12px",
                }}
                autoComplete="off"
              />
              {resourceFilter && (
                <button
                  type="button"
                  onClick={() => setResourceFilter("")}
                  style={{
                    border: "none",
                    background: "#f3f4f6",
                    color: "#6b7280",
                    borderRadius: "4px",
                    padding: "6px 10px",
                    fontSize: "12px",
                    cursor: "pointer",
                  }}
                >
                  Clear
                </button>
              )}
            </div>
            <datalist id="resource-filter-options">
              {resourceFilterOptions.map(name => (
                <option key={name} value={name} />
              ))}
            </datalist>
          </label>
          <label style={{ fontSize: "12px", fontWeight: 600, color: "#1f2937", display: "flex", flexDirection: "column", gap: "8px" , marginLeft: "10px" }}>
            Category
            <select
              value={filterCategory ?? ""}
              onChange={(e) => setFilterCategory(e.target.value || null)}
              style={{ padding: "6px 8px", borderRadius: "4px", border: "1px solid #d1d5db", fontSize: "12px" }}
            >
              <option value="">All</option>
              {categoryBreakdown.map(cat => (
                <option key={cat.name} value={cat.name}>
                  {cat.name}
                </option>
              ))}
            </select>
          </label>
          <label style={{ fontSize: "12px", fontWeight: 600, color: "#1f2937", display: "flex", flexDirection: "column", gap: "8px", marginLeft: "10px" }}>
            Impact
            <select
              value={filterImpact ?? ""}
              onChange={(e) => setFilterImpact(e.target.value || null)}
              style={{ padding: "6px 8px", borderRadius: "4px", border: "1px solid #d1d5db", fontSize: "12px" }}
            >
              <option value="">All</option>
              {impactBreakdown.map(impact => (
                <option key={impact.name} value={impact.name}>
                  {impact.name}
                </option>
              ))}
            </select>
          </label>
        </div>

        {/* Findings Table */}
        <div
          style={{
            overflowX: "auto",
            border: "1px solid #e5e7eb",
            borderRadius: "8px",
          }}
        >
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: "12px",
            }}
          >
            <thead>
              <tr
                style={{
                  background: "#f9fafb",
                  borderBottom: "2px solid #e5e7eb",
                }}
              >
                <th
                  style={{
                    padding: "12px",
                    textAlign: "left",
                    fontWeight: 600,
                    color: "#374151",
                  }}
                >
                  <button
                    type="button"
                    onClick={() => toggleSort("resource")}
                    style={{
                      width: "100%",
                      border: "none",
                      background: "transparent",
                      padding: 0,
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      fontWeight: 600,
                      fontSize: "12px",
                      color: "inherit",
                      cursor: "pointer",
                    }}
                  >
                    <span>Resource</span>
                    <span style={{ fontSize: "10px", color: "#6b7280" }}>{sortIndicator("resource")}</span>
                  </button>
                </th>
                <th
                  style={{
                    padding: "12px",
                    textAlign: "left",
                    fontWeight: 600,
                    color: "#374151",
                  }}
                >
                  <button
                    type="button"
                    onClick={() => toggleSort("recommendation")}
                    style={{
                      width: "100%",
                      border: "none",
                      background: "transparent",
                      padding: 0,
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      fontWeight: 600,
                      fontSize: "12px",
                      color: "inherit",
                      cursor: "pointer",
                    }}
                  >
                    <span>Recommendation</span>
                    <span style={{ fontSize: "10px", color: "#6b7280" }}>{sortIndicator("recommendation")}</span>
                  </button>
                </th>
                <th
                  style={{
                    padding: "12px",
                    textAlign: "left",
                    fontWeight: 600,
                    color: "#374151",
                  }}
                >
                  <button
                    type="button"
                    onClick={() => toggleSort("benefit")}
                    style={{
                      width: "100%",
                      border: "none",
                      background: "transparent",
                      padding: 0,
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      fontWeight: 600,
                      fontSize: "12px",
                      color: "inherit",
                      cursor: "pointer",
                    }}
                  >
                    <span>Benefit</span>
                    <span style={{ fontSize: "10px", color: "#6b7280" }}>{sortIndicator("benefit")}</span>
                  </button>
                </th>
                <th
                  style={{
                    padding: "12px",
                    textAlign: "left",
                    fontWeight: 600,
                    color: "#374151",
                  }}
                >
                  <button
                    type="button"
                    onClick={() => toggleSort("category")}
                    style={{
                      width: "100%",
                      border: "none",
                      background: "transparent",
                      padding: 0,
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      fontWeight: 600,
                      fontSize: "12px",
                      color: "inherit",
                      cursor: "pointer",
                    }}
                  >
                    <span>Category</span>
                    <span style={{ fontSize: "10px", color: "#6b7280" }}>{sortIndicator("category")}</span>
                  </button>
                </th>
                <th
                  style={{
                    padding: "12px",
                    textAlign: "center",
                    fontWeight: 600,
                    color: "#374151",
                  }}
                >
                  <button
                    type="button"
                    onClick={() => toggleSort("impact")}
                    style={{
                      width: "100%",
                      border: "none",
                      background: "transparent",
                      padding: 0,
                      display: "flex",
                      justifyContent: "center",
                      alignItems: "center",
                      gap: "6px",
                      fontWeight: 600,
                      fontSize: "12px",
                      color: "inherit",
                      cursor: "pointer",
                    }}
                  >
                    <span>Impact</span>
                    <span style={{ fontSize: "10px", color: "#6b7280" }}>{sortIndicator("impact")}</span>
                  </button>
                </th>
                <th
                  style={{
                    padding: "12px",
                    textAlign: "center",
                    fontWeight: 600,
                    color: "#374151",
                  }}
                >
                  <button
                    type="button"
                    onClick={() => toggleSort("status")}
                    style={{
                      width: "100%",
                      border: "none",
                      background: "transparent",
                      padding: 0,
                      display: "flex",
                      justifyContent: "center",
                      alignItems: "center",
                      gap: "6px",
                      fontWeight: 600,
                      fontSize: "12px",
                      color: "inherit",
                      cursor: "pointer",
                    }}
                  >
                    <span>Status</span>
                    <span style={{ fontSize: "10px", color: "#6b7280" }}>{sortIndicator("status")}</span>
                  </button>
                </th>
              </tr>
            </thead>
            <tbody>
              {filteredFindings.map((finding, idx) => (
                <tr
                  key={idx}
                  title={finding.long_description || ""}
                  style={{
                    borderBottom: "1px solid #e5e7eb",
                    background: idx % 2 === 0 ? "#fff" : "#f9fafb",
                  }}
                >
                  <td style={{ padding: "12px", color: "#374151" }}>
                    <div style={{ fontWeight: 600 }}>
                      {getResourceDisplayName(finding.resourceId, annotationMap.get(finding.resourceId)?.display_name || finding.resourceName)}
                    </div>
                    <div
                      style={{
                        fontSize: "11px",
                        color: "#6b7280",
                        marginTop: "2px",
                      }}
                    >
                      {annotationMap.get(finding.resourceId)?.azure_service_category || finding.resourceId.split("/")[7]}
                    </div>
                  </td>
                  <td style={{ padding: "12px", color: "#374151" }}>
                    {finding.description}
                  </td>
                  <td style={{ padding: "12px", color: "#374151" }}>
                    {finding.learn_more && finding.learn_more.length > 0 ? (
                      <a
                        href={finding.learn_more[0].url}
                        target="_blank"
                        rel="noreferrer"
                        style={{ color: "#2563eb", textDecoration: "none", fontWeight: 600 }}
                        title={finding.learn_more[0].name || "Learn more"}
                      >
                        {finding.potential_benefits || "—"}
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td style={{ padding: "12px", color: "#374151" }}>
                    {finding.category}
                  </td>
                  <td
                    style={{
                      padding: "12px",
                      textAlign: "center",
                      color: getImpactColor(finding.impact),
                      fontWeight: 600,
                    }}
                  >
                    {finding.impact}
                  </td>
                  <td
                    style={{
                      padding: "12px",
                      textAlign: "center",
                    }}
                  >
                    <span
                      style={{
                        display: "inline-block",
                        padding: "4px 8px",
                        borderRadius: "4px",
                        background: finding.status === "pass" ? "#ecfdf5" : "#fee2e2",
                        color: getStatusColor(finding.status),
                        fontWeight: 600,
                        fontSize: "11px",
                      }}
                    >
                      {finding.status.toUpperCase()}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {filteredFindings.length === 0 && (
          <div
            style={{
              textAlign: "center",
              padding: "32px",
              color: "#6b7280",
            }}
          >
            No findings match the selected filter.
          </div>
        )}
      </div>
    </div>
  );
};

export default ResilienceSummary;
