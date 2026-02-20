import React, { useState, useMemo, useEffect } from "react";
import {
  ZonalResiliencyResponse,
  ResourceZonalAnalysis,
  DeploymentPattern,
  getDeploymentPatternLabel,
  getDeploymentPatternColor,
  getDeploymentPatternIcon,
} from "../api/resilience";
import { getElementWeight } from "../utils/resilienceScore";

interface LLMAnnotation {
  display_name?: string;
  azure_service_category?: string;
}

interface ResiliencyCheck {
  recommendation_id: string;
  description: string;
  category: string;
  impact: string;
  status: "pass" | "fail";
  llm_reasoning?: string;
  heuristic_reasoning?: string;
}

interface ResourceEvaluation {
  resource_id: string;
  checks: ResiliencyCheck[];
}

interface ResiliencyEvaluations {
  [resourceId: string]: ResourceEvaluation;
}

interface ZonalResiliencySummaryProps {
  data: ZonalResiliencyResponse;
  graphData?: {
    nodes?: Array<{ id: string; name?: string; type?: string; metadata?: Record<string, unknown> }>;
    llm_annotations?: {
      nodes?: Array<{
        node_id: string;
        annotations: LLMAnnotation;
      }>;
    };
  };
  resourceGroupFilter?: Set<string>;
  serviceFilter?: Set<string>;
  onResourceSelect?: (resourceId: string) => void;
}

// Score donut visualization - matches ResiliencySummary style
const ScoreDonut: React.FC<{
  score: number; // 0.0-1.0
  size?: number;
  label?: string;
}> = ({ score, size = 140, label = "Zonal Score" }) => {
  const percentage = score * 100;
  const angle = (percentage / 100) * 360;

  // Color based on score
  const getScoreColor = () => {
    if (percentage >= 80) return "#10b981"; // Green
    if (percentage >= 60) return "#f59e0b"; // Orange
    if (percentage >= 40) return "#f97316"; // Dark orange
    return "#dc2626"; // Red
  };

  const scoreColor = getScoreColor();

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "12px" }}>
      <div
        style={{
          position: "relative",
          width: `${size}px`,
          height: `${size}px`,
          borderRadius: "50%",
          background: `conic-gradient(from 0deg, ${scoreColor} 0deg ${angle}deg, #e5e7eb ${angle}deg 360deg)`,
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
          <span style={{ fontSize: "20px", fontWeight: 700, color: "#1f2937" }}>
            {percentage.toFixed(0)}%
          </span>
          <span style={{ fontSize: "10px", color: "#6b7280" }}>{label}</span>
        </div>
      </div>
    </div>
  );
};

// Pattern badge - matches ResiliencySummary style
const PatternBadge: React.FC<{ pattern: DeploymentPattern }> = ({ pattern }) => {
  const bgColor = getDeploymentPatternColor(pattern);

  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "6px",
        padding: "4px 10px",
        backgroundColor: bgColor + "20",
        color: bgColor,
        borderRadius: "6px",
        fontSize: "12px",
        fontWeight: 600,
        border: `1px solid ${bgColor}40`,
      }}
    >
      <span style={{ fontSize: "14px" }}>{getDeploymentPatternIcon(pattern)}</span>
      {getDeploymentPatternLabel(pattern)}
    </div>
  );
};

// Metric card - matches ResiliencySummary style
const MetricCard: React.FC<{
  title: string;
  value: number;
  total: number;
  background: string;
  color: string;
}> = ({ title, value, total, background, color }) => {
  const percentage = total > 0 ? (value / total) * 100 : 0;

  return (
    <div
      style={{
        padding: "16px",
        borderRadius: "8px",
        borderLeft: `4px solid ${color}`,
        flex: 1,
        minWidth: "150px",
        backgroundColor: background,
      }}
    >
      <div style={{ fontSize: "12px", fontWeight: 600, color: "#6b7280", marginBottom: "8px" }}>
        {title}
      </div>
      <div style={{ fontSize: "20px", fontWeight: 700, color: color }}>
        {value}
      </div>
      <div style={{ fontSize: "11px", color: "#6b7280", marginTop: "4px" }}>
        {percentage.toFixed(1)}% of {total} resources
      </div>
    </div>
  );
};

// Resource row component
const ResourceRow: React.FC<{
  resource: ResourceZonalAnalysis;
  index: number;
  annotationMap?: Map<string, LLMAnnotation>;
  evaluations?: ResiliencyEvaluations;
  onResourceSelect?: (resourceId: string) => void;
}> = ({ resource, index, annotationMap, evaluations, onResourceSelect }) => {
  const { zonal_data } = resource;
  
  // Get display name from annotations or fall back to resource_name
  const annotation = annotationMap?.get(resource.resource_id);
  const displayName = annotation?.display_name || resource.resource_name;
  // Get category from annotations or derive from resource_type
  const category = annotation?.azure_service_category || resource.resource_type.split("/").pop()?.toUpperCase() || "Unknown";

  // Extract recommendation for non-compliant resources
  const getRecommendation = (): string => {
    // Not applicable resources don't need recommendation
    if (zonal_data.deployment_pattern === "not_applicable") {
      return "";
    }

    // If compliant, no recommendation needed
    if (zonal_data.meets_3az_requirement) {
      return "";
    }

    // For non-compliant resources, get relevant failure reasons from evaluations
    const evaluation = evaluations?.[resource.resource_id];
    if (!evaluation?.checks) {
      return "Zone redundancy not configured";
    }

    // Find zone-related failures
    const zoneFailures = evaluation.checks.filter(check => {
      const desc = check.description.toLowerCase();
      const status = check.status === "fail";
      return status && (
        desc.includes("zone") || 
        desc.includes("redundancy") || 
        desc.includes("availability") ||
        desc.includes("resilience")
      );
    });

    if (zoneFailures.length === 0) {
      // Fallback to general pattern-based message
      if (zonal_data.deployment_pattern === "single_zone") {
        return "Deployed in single zone - needs multi-zone or zone-redundant configuration";
      }
      return "Not compliant with 3-AZ requirement";
    }

    // Use LLM or heuristic reasoning if available, otherwise use description
    const recommendation = zoneFailures[0].llm_reasoning || zoneFailures[0].heuristic_reasoning || zoneFailures[0].description;
    return recommendation;
  };

  const recommendation = getRecommendation();

  return (
    <tr
      style={{
        borderBottom: "1px solid #e5e7eb",
        backgroundColor: index % 2 === 0 ? "#ffffff" : "#f9fafb",
      }}
    >
      <td style={{ padding: "12px", fontSize: "12px" }}>
        <button
          type="button"
          onClick={() => onResourceSelect?.(resource.resource_id)}
          style={{
            fontWeight: 600,
            color: "#1f2937",
            border: "none",
            background: "transparent",
            padding: 0,
            margin: 0,
            cursor: onResourceSelect ? "pointer" : "default",
            textAlign: "left",
          }}
          title={resource.resource_id}
        >
          {displayName}
        </button>
        <div style={{ fontSize: "11px", color: "#6b7280", marginTop: "2px" }}>
          {category}
        </div>
      </td>
      <td style={{ padding: "12px", fontSize: "12px", color: "#6b7280" }}>
        {resource.location}
      </td>
      <td style={{ padding: "12px" }}>
        <PatternBadge pattern={zonal_data.deployment_pattern} />
      </td>
      <td style={{ padding: "12px", fontSize: "12px" }}>
        {zonal_data.zone_count > 0 ? (
          <span 
            style={{ color: "#6b7280", fontSize: "11px", cursor: "help" }}
            title={zonal_data.zones_used.length > 0 ? `Zones: ${zonal_data.zones_used.join(", ")}` : "Zone information not available"}
          >
            {zonal_data.zone_count} zone{zonal_data.zone_count > 1 ? "s" : ""}
          </span>
        ) : (
          <span style={{ color: "#6b7280" }}>—</span>
        )}
      </td>
      <td style={{ padding: "12px", textAlign: "center" }}>
        <div
          style={{
            color: zonal_data.meets_3az_requirement ? "#10b981" : "#f59e0b",
            fontWeight: 600,
            fontSize: "14px",
          }}
        >
          {zonal_data.meets_3az_requirement ? "✓" : "⚠"}
        </div>
      </td>
      <td style={{ padding: "12px", fontSize: "12px", color: "#5a4a4a", maxWidth: "300px" }}>
        {recommendation && (
          <div style={{ wordBreak: "break-word", lineHeight: "1.4" }}>
            {recommendation}
          </div>
        )}
      </td>
    </tr>
  );
};

// Main Component
const ZonalResiliencySummary: React.FC<ZonalResiliencySummaryProps> = ({ data, graphData, resourceGroupFilter, serviceFilter, onResourceSelect }) => {
  const [sortBy, setSortBy] = useState<"name" | "pattern" | "compliance">("name");
  const [filterPattern, setFilterPattern] = useState<DeploymentPattern | "all">("all");
  const [evaluations, setEvaluations] = useState<ResiliencyEvaluations>({});
  const [loadingEvals, setLoadingEvals] = useState(false);

  // Load resilience evaluations on component mount
  useEffect(() => {
    const loadEvaluations = async () => {
      try {
        setLoadingEvals(true);
        // Extract subscription ID from any resource ID in the data
        const subscriptionId = data.resources[0]?.resource_id?.split("/")[2];
        if (!subscriptionId) {
          console.warn("Could not extract subscription ID from resources");
          return;
        }

        const response = await fetch(`/api/subscriptions/${subscriptionId}/zonal-resilience/evaluations`);
        if (response.ok) {
          const data = await response.json();
          setEvaluations(data.evaluations || {});
        }
      } catch (error) {
        console.warn("Could not load resilience evaluations:", error);
        // Silently fail - evaluations are optional
      } finally {
        setLoadingEvals(false);
      }
    };

    if (data.resources.length > 0) {
      loadEvaluations();
    }
  }, [data.resources]);

  // Create annotation lookup from graphData
  const annotationMap = useMemo(() => {
    const map = new Map<string, LLMAnnotation>();
    if (graphData?.llm_annotations?.nodes) {
      graphData.llm_annotations.nodes.forEach(node => {
        map.set(node.node_id, node.annotations);
      });
    }
    return map;
  }, [graphData]);

  // Calculate summary from resources with weighted scoring
  const calculateSummary = (resources: ResourceZonalAnalysis[], annotations?: Map<string, LLMAnnotation>) => {
    const zoneRedundant = resources.filter((r) => r.zonal_data.deployment_pattern === "zone_redundant").length;
    const multiZone = resources.filter((r) => r.zonal_data.deployment_pattern === "multi_zone").length;
    const singleZone = resources.filter((r) => r.zonal_data.deployment_pattern === "single_zone").length;
    const notApplicable = resources.filter((r) => r.zonal_data.deployment_pattern === "not_applicable").length;
    const unknown = resources.filter((r) => r.zonal_data.deployment_pattern === "unknown").length;
    
    const total = resources.length;
    
    // Exclude not_applicable from compliance calculation
    const applicableResources = resources.filter((r) => r.zonal_data.deployment_pattern !== "not_applicable");
    const compliant = applicableResources.filter((r) => r.zonal_data.meets_3az_requirement).length;
    const applicableCount = applicableResources.length;
    const compliancePercent = applicableCount > 0 ? (compliant / applicableCount) * 100 : 0;
    
    // Zonal resilience score: weighted by resource criticality
    // Pattern weights: zone_redundant=1.0, multi_zone=0.8, single_zone=0.3, others=0.0
    let totalWeight = 0;
    let weightedScore = 0;
    
    applicableResources.forEach((resource) => {
      // Get element weight (criticality) from annotations, default to 1.0
      const elementWeight = annotations 
        ? getElementWeight(resource.resource_id, annotations) 
        : 1.0;
      
      // Get pattern score
      let patternScore = 0;
      switch (resource.zonal_data.deployment_pattern) {
        case "zone_redundant":
          patternScore = 1.0;
          break;
        case "multi_zone":
          patternScore = 0.8;
          break;
        case "single_zone":
          patternScore = 0.3;
          break;
        default:
          patternScore = 0.0;
      }
      
      // Weighted contribution: pattern_score * element_weight
      const resourceWeight = patternScore * elementWeight;
      totalWeight += elementWeight;
      weightedScore += resourceWeight;
    });
    
    const finalScore = totalWeight > 0 ? weightedScore / totalWeight : 0;
    
    return {
      total_resources: total,
      zone_redundant_resources: zoneRedundant,
      multi_zone_resources: multiZone,
      single_zone_resources: singleZone,
      not_applicable_resources: notApplicable,
      unknown_zone_resources: unknown,
      compliant_3az_resources: compliant,
      overall_3az_compliant: compliant === applicableCount && applicableCount > 0,
      zonal_resilience_score: finalScore,
      compliance_percentage: compliancePercent,
    };
  };

  // Helper to get resource group from resource ID
  const getResourceGroup = (resourceId: string): string => {
    const parts = resourceId.split("/").filter(p => p);
    const partsLower = parts.map(p => p.toLowerCase());
    const rgIndex = partsLower.indexOf("resourcegroups");
    if (rgIndex !== -1 && rgIndex + 1 < parts.length) {
      return String(parts[rgIndex + 1]).toLowerCase();
    }
    return "";
  };

  // Helper to get service type from resource
  const getServiceType = (resourceId: string, resourceType: string): string => {
    // Try to get from graph nodes first
    const node = graphData?.nodes?.find(n => String(n?.id ?? "").toLowerCase() === resourceId.toLowerCase());
    if (node?.type) {
      return String(node.type).toLowerCase();
    }
    // Fallback to resource type normalization
    return String(resourceType).split("/").pop()?.toLowerCase() || "";
  };

  // Apply filters from sidebar (resource groups and services)
  const sidebarFiltered = useMemo(() => {
    return data.resources.filter((resource) => {
      // Exclude NOT_APPLICABLE resources (VNets, subnets, SQL Servers - infrastructure/containers)
      // These are regional resources with no zone configuration options
      if (resource.zonal_data.deployment_pattern === "not_applicable") {
        return false;
      }

      // Resource group filter
      if (resourceGroupFilter && resourceGroupFilter.size > 0) {
        const rg = getResourceGroup(resource.resource_id);
        if (rg && !resourceGroupFilter.has(rg)) {
          return false;
        }
      }

      // Service filter
      if (serviceFilter && serviceFilter.size > 0) {
        const serviceType = getServiceType(resource.resource_id, resource.resource_type);
        if (serviceType && !serviceFilter.has(serviceType)) {
          return false;
        }
      }

      return true;
    });
  }, [data.resources, resourceGroupFilter, serviceFilter, graphData?.nodes]);

  // Filter and sort resources
  const filteredAndSorted = useMemo(() => {
    let filtered = sidebarFiltered;

    // Apply pattern filter
    if (filterPattern !== "all") {
      filtered = filtered.filter((r) => r.zonal_data.deployment_pattern === filterPattern);
    }

    // Apply sorting
    return filtered.sort((a, b) => {
      switch (sortBy) {
        case "name":
          return a.resource_name.localeCompare(b.resource_name);
        case "pattern":
          return a.zonal_data.deployment_pattern.localeCompare(b.zonal_data.deployment_pattern);
        case "compliance":
          return a.zonal_data.meets_3az_requirement === b.zonal_data.meets_3az_requirement
            ? 0
            : a.zonal_data.meets_3az_requirement
              ? -1
              : 1;
        default:
          return 0;
      }
    });
  }, [sidebarFiltered, sortBy, filterPattern]);

  // Use provided summary or calculate from filtered resources with weighted scoring
  const summary = data.summary || calculateSummary(sidebarFiltered, annotationMap);

  return (
    <div style={{ padding: "24px", paddingBottom: "64px", background: "#f9fafb", minHeight: "100vh" }}>
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
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "auto 1fr",
            gap: "24px",
            alignItems: "flex-start",
          }}
        >
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "12px" }}>
                <ScoreDonut score={summary.zonal_resilience_score} size={120} label="Zonal Score" />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: "10px", marginTop: "12px" }}>
                <MetricCard
                    title="Multi-Zone"
                    value={summary.multi_zone_resources}
                    total={summary.total_resources}
                    background="#dee7f3"
                    color="#448eef"
                />
                <MetricCard
                    title="Zone Redundant"
                    value={summary.zone_redundant_resources}
                    total={summary.total_resources}
                    background="#d0fbe7"
                    color="#10b981"
                />
                <MetricCard
                    title="Single Zone"
                    value={summary.single_zone_resources}
                    total={summary.total_resources}
                    background="#ffe7c6"
                    color="#f59e0b"
                />
                <MetricCard
                    title="Unknown Configuration"
                    value={summary.unknown_zone_resources}
                    total={summary.total_resources}
                    background="#e6d5fc"
                    color="#8b5cf6"
                />
                <MetricCard
                    title="Not Applicable"
                    value={summary.not_applicable_resources}
                    total={summary.total_resources}
                    background="#d9d9d9"
                    color="#aeb1b4"
                />
                </div>
            </div>

        </div>
      {/* Resources Table Section */}
      <div
        style={{
          background: "#fff",
          borderRadius: "12px",
          padding: "24px",
          boxShadow: "0 1px 3px rgba(0,0,0,0.1)",
          border: "1px solid #e5e7eb",
        }}
      >
        <div style={{ marginBottom: "16px" }}>
          <h2
            style={{
              margin: "0 0 16px",
              fontSize: "16px",
              fontWeight: 700,
              color: "#1f2937",
            }}
          >
            Resources ({filteredAndSorted.length})
          </h2>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
            {/* Filter by Pattern */}
            <div>
              <label style={{ fontSize: "12px", fontWeight: 600, color: "#6b7280", display: "block", marginBottom: "6px" }}>
                Filter by Pattern
              </label>
              <select
                value={filterPattern}
                onChange={(e) => setFilterPattern(e.target.value as DeploymentPattern | "all")}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  borderRadius: "6px",
                  border: "1px solid #d1d5db",
                  fontSize: "12px",
                }}
              >
                <option value="all">All Patterns</option>
                <option value="zone_redundant">Zone Redundant</option>
                <option value="multi_zone">Multi-Zone</option>
                <option value="single_zone">Single Zone</option>
                <option value="unknown">Unknown</option>
                <option value="not_applicable">Not Applicable</option>
              </select>
            </div>

            {/* Sort by */}
            <div>
              <label style={{ fontSize: "12px", fontWeight: 600, color: "#6b7280", display: "block", marginBottom: "6px" }}>
                Sort by
              </label>
              <select
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as "name" | "pattern" | "compliance")}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  borderRadius: "6px",
                  border: "1px solid #d1d5db",
                  fontSize: "12px",
                }}
              >
                <option value="name">Resource Name</option>
                <option value="pattern">Deployment Pattern</option>
                <option value="compliance">3-AZ Compliance</option>
              </select>
            </div>
          </div>
        </div>

        {/* Table */}
        <div style={{ overflowX: "auto" }}>
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: "12px",
            }}
          >
            <thead>
              <tr style={{ borderBottom: "2px solid #e5e7eb", backgroundColor: "#f9fafb" }}>
                <th style={{ padding: "12px", textAlign: "left", fontWeight: 600, color: "#6b7280" }}>
                  Resource Name
                </th>
                <th style={{ padding: "12px", textAlign: "left", fontWeight: 600, color: "#6b7280" }}>
                  Location
                </th>
                <th style={{ padding: "12px", textAlign: "left", fontWeight: 600, color: "#6b7280" }}>
                  Pattern
                </th>
                <th style={{ padding: "12px", textAlign: "left", fontWeight: 600, color: "#6b7280" }}>
                  Zones
                </th>
                <th style={{ padding: "12px", textAlign: "center", fontWeight: 600, color: "#6b7280" }}>
                  3-AZ Compliant
                </th>
                <th style={{ padding: "12px", textAlign: "left", fontWeight: 600, color: "#6b7280" }}>
                  Recommendation
                </th>
              </tr>
            </thead>
            <tbody>
              {filteredAndSorted.map((resource, index) => (
                <ResourceRow key={resource.resource_id} resource={resource} index={index} annotationMap={annotationMap} evaluations={evaluations} onResourceSelect={onResourceSelect} />
              ))}
            </tbody>
          </table>
        </div>

        {filteredAndSorted.length === 0 && (
          <div style={{ padding: "24px", textAlign: "center", color: "#6b7280" }}>
            No resources found with the selected filter.
          </div>
        )}
      </div>

      {/* Recommendations Section */}
      {data.resources.length > 0 && (
        <div
          style={{
            background: "#fff",
            borderRadius: "12px",
            padding: "24px",
            marginTop: "24px",
            marginBottom: "24px",
            boxShadow: "0 1px 3px rgba(0,0,0,0.1)",
            border: "1px solid #e5e7eb",
          }}
        >
          <h2
            style={{
              margin: "0 0 16px",
              fontSize: "16px",
              fontWeight: 700,
              color: "#1f2937",
            }}
          >
            Recommendations
          </h2>

          <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: "12px" }}>
            {summary.single_zone_resources > 0 && (
              <div style={{ padding: "12px", backgroundColor: "#fef3c7", borderLeft: "4px solid #f59e0b", borderRadius: "4px" }}>
                <div style={{ fontWeight: 600, color: "#92400e" }}>
                  Deploy {summary.single_zone_resources} single-zone resource{summary.single_zone_resources > 1 ? "s" : ""} across multiple availability zones
                </div>
                <div style={{ fontSize: "12px", color: "#b45309", marginTop: "4px" }}>
                  This will improve resilience and availability by distributing workloads across zones.
                </div>
              </div>
            )}

            {summary.multi_zone_resources > 0 && summary.compliant_3az_resources < summary.total_resources && (
              <div style={{ padding: "12px", backgroundColor: "#dbeafe", borderLeft: "4px solid #3b82f6", borderRadius: "4px" }}>
                <div style={{ fontWeight: 600, color: "#1e40af" }}>
                  Extend {summary.multi_zone_resources} multi-zone resource{summary.multi_zone_resources > 1 ? "s" : ""} to all 3 availability zones
                </div>
                <div style={{ fontSize: "12px", color: "#1e3a8a", marginTop: "4px" }}>
                  Ensure maximum resilience by utilizing all available zones in your region.
                </div>
              </div>
            )}

            {summary.unknown_zone_resources > 0 && (
              <div style={{ padding: "12px", backgroundColor: "#ede9fe", borderLeft: "4px solid #8b5cf6", borderRadius: "4px" }}>
                <div style={{ fontWeight: 600, color: "#5b21b6" }}>
                  Review configuration of {summary.unknown_zone_resources} resource{summary.unknown_zone_resources > 1 ? "s" : ""} with unknown zone setup
                </div>
                <div style={{ fontSize: "12px", color: "#6d28d9", marginTop: "4px" }}>
                  Verify resource placement and zone configuration to ensure proper redundancy.
                </div>
              </div>
            )}

            {summary.overall_3az_compliant && (
              <div style={{ padding: "12px", backgroundColor: "#dcfce7", borderLeft: "4px solid #10b981", borderRadius: "4px" }}>
                <div style={{ fontWeight: 600, color: "#166534" }}>✓ All applicable resources are 3-AZ compliant</div>
                <div style={{ fontSize: "12px", color: "#15803d", marginTop: "4px" }}>
                  Your workload has excellent cross-zone resilience coverage.
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default ZonalResiliencySummary;
