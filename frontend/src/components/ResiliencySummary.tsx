import React, { useState, useMemo, useEffect, useCallback, useRef } from "react";
import * as XLSX from "xlsx";
import { canonicalTypeForNode, normalizeTypeString, LEVEL_TO_MAX_IMPORTANCE, type ViewLevel } from "../domain/graphView";
import { saveOverride, getOverrides, deleteOverride, saveBatchOverrides, type ResiliencyCheck, type BatchOverrideItem } from "../api/resilience";
import { calculateResiliencyScore, getElementWeight as getElementWeightUtil, DEFAULT_WEIGHTS, type ResiliencyWeights } from "../utils/resilienceScore";

interface ResiliencyEvaluation {
  resource_id: string;
  resource_type: string;
  resource_name: string;
  checks: ResiliencyCheck[];
  findings: ResiliencyCheck[];
}

interface ResiliencyOverride {
  resource_id: string;
  recommendation_id: string;
  status: "pass" | "fail" | "pending";
  overridden_at?: string;
  overridden_by?: string;
  resilience_check_id?: string;
}

interface LLMAnnotation {
  display_name?: string;
  azure_service_category?: string;
  azure_service_name?: string;
  criticality_weight?: number;
  criticality_score?: number;
  confidence?: number;
  hide_by_default?: boolean;
  layer?: number;
}

interface ResiliencySummaryProps {
  evaluations: Record<string, ResiliencyEvaluation>;
  workloadScore?: number;  // Overall resilience score (0.0-1.0)
  subscriptionId?: string;  // Needed for saving overrides
  subscriptionOptions?: Array<{ id: string; name: string }>;
  graphData?: {
    nodes?: Array<{ id: string; name?: string; type?: string; metadata?: Record<string, unknown> }>;
    node_overrides?: Record<string, Record<string, unknown>>;
    llm_annotations?: {
      nodes?: Array<{
        node_id: string;
        annotations: LLMAnnotation;
      }>;
    };
  };
  overrides?: Record<string, ResiliencyOverride>;
  viewLevel?: ViewLevel;
  resourceGroupFilter?: Set<string>;
  serviceFilter?: Set<string>;
  validationSourceFilter?: Set<string>;
  highlightRecommendationId?: string;
  highlightRecommendationTitle?: string;
  onOverrideSaved?: (override: ResiliencyOverride) => void;
  onOverrideDeleted?: (resilienceCheckId: string, resourceId?: string) => void;
  onShowInGraph?: (resourceId: string) => void;
  onResourceSelect?: (resourceId: string) => void;
}

const buildOverrideMap = (overrides?: Record<string, ResiliencyOverride>) => {
  const overrideMap: Record<string, { status: "pass" | "fail" | "pending"; validation_source: string; resilience_check_id?: string }> = {};
  if (!overrides) return overrideMap;

  Object.entries(overrides).forEach(([resilience_check_id, override]) => {
    const validationSource = (override.overridden_by || "").toLowerCase() === "user"
      ? "User"
      : override.overridden_by || "";

    // Use resilience_check_id as the key instead of resource_id_recommendation_id
    overrideMap[resilience_check_id] = {
      status: override.status,
      validation_source: validationSource,
      resilience_check_id: resilience_check_id,
    };
  });

  return overrideMap;
};

// Resiliency score donut (0-100% gradient)
const ScoreDonut: React.FC<{
  score: number;  // 0.0-1.0
  size?: number;
}> = ({ score, size = 140 }) => {
  const percentage = score * 100;
  const angle = (percentage / 100) * 360;
  
  // Color based on score
  const getScoreColor = () => {
    if (percentage >= 80) return "#10b981";  // Green
    if (percentage >= 60) return "#f59e0b";  // Orange
    if (percentage >= 40) return "#f97316";  // Dark orange
    return "#dc2626";  // Red
  };

  const scoreColor = getScoreColor();
  
  return (
    <div
      style={{
        position: "relative",
        width: `${size}px`,
        height: `${size}px`,
        borderRadius: "50%",
        background: `conic-gradient(
          from 0deg,
          ${scoreColor} 0deg ${angle}deg,
          #e5e7eb ${angle}deg 360deg
        )`,
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
          {percentage.toFixed(0)}%
        </span>
        <span style={{ fontSize: "10px", color: "#6b7280" }}>Resiliency</span>
      </div>
    </div>
  );
};

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

type SortColumn = "subscription" | "resource" | "recommendation" | "category" | "impact" | "status" | "benefit" | "weight" | "validated_by";

const ResiliencySummary: React.FC<ResiliencySummaryProps> = ({
  evaluations,
  workloadScore,
  subscriptionId,
  subscriptionOptions,
  graphData,
  overrides,
  viewLevel,
  resourceGroupFilter,
  serviceFilter,
  validationSourceFilter,
  highlightRecommendationId,
  highlightRecommendationTitle,
  onOverrideSaved,
  onOverrideDeleted,
  onShowInGraph,
  onResourceSelect,
}) => {
  const [expandedResource, setExpandedResource] = useState<string | null>(null);
  const [filterStatus, setFilterStatus] = useState<"all" | "pass" | "fail" | "pending">("fail");
  const [breakdownView, setBreakdownView] = useState<"category" | "impact" | "service" | "subscription">("category");
  const [sortColumn, setSortColumn] = useState<SortColumn>("weight");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("desc");
  const [filterCategory, setFilterCategory] = useState<string | null>(null);
  const [filterImpact, setFilterImpact] = useState<string | null>(null);
  const [filterValidationSource, setFilterValidationSource] = useState<string | null>(null);
  const [filterSubscription, setFilterSubscription] = useState<string | null>(null);
  const [resourceFilter, setResourceFilter] = useState("");
  const [userOverrides, setUserOverrides] = useState<Record<string, { status: "pass" | "fail" | "pending"; validation_source: string; resilience_check_id?: string }>>({});
  const [visibleTooltip, setVisibleTooltip] = useState<string | null>(null);

  const getRecommendationRowId = (recommendationId: string): string => {
    return `finding-rec-${recommendationId.replace(/[^a-zA-Z0-9\-_.:]/g, "_")}`;
  };

  const normalizeRecommendationText = (value?: string): string => {
    return (value || "").trim().toLowerCase();
  };

  const isHighlightedRecommendation = (recommendationId?: string, description?: string): boolean => {
    const normalizedHighlightId = normalizeRecommendationText(highlightRecommendationId);
    const normalizedHighlightTitle = normalizeRecommendationText(highlightRecommendationTitle);
    const normalizedId = normalizeRecommendationText(recommendationId);
    const normalizedDescription = normalizeRecommendationText(description);

    if (normalizedHighlightId && normalizedHighlightId === normalizedId) return true;
    if (normalizedHighlightTitle && normalizedHighlightTitle === normalizedDescription) return true;
    return false;
  };

  // Fetch weights from backend once and reuse across all calculations
  const [categoryWeights, setCategoryWeights] = useState<Record<string, number>>(DEFAULT_WEIGHTS.categoryWeights);
  const [impactWeights, setImpactWeights] = useState<Record<string, number>>(DEFAULT_WEIGHTS.impactWeights);

  // Load weights from backend
  useEffect(() => {
    const loadWeights = async () => {
      try {
        const response = await fetch('/api/resilience/weights');
        if (response.ok) {
          const data = await response.json();
          if (data.category_weights) {
            setCategoryWeights(data.category_weights);
          }
          if (data.impact_weights) {
            setImpactWeights(data.impact_weights);
          }
        }
      } catch (error) {
        console.error('Failed to load weights from backend, using defaults:', error);
      }
    };
    loadWeights();
  }, []);

  // Use ref to track initialization
  const initializeRef = useRef(false);

  // Keep local overrides in sync with parent-provided overrides
  useEffect(() => {
    if (overrides) {
      setUserOverrides(buildOverrideMap(overrides));
    }
  }, [overrides]);

  // Load evaluations and overrides from localStorage on mount - single source of truth
  useEffect(() => {
    if (initializeRef.current) return;
    initializeRef.current = true;

    if (!subscriptionId) return;

    const loadFromStorage = async () => {
      try {
        const storageKey = `resilience_${subscriptionId}`;
        const stored = localStorage.getItem(storageKey);
        
        if (stored) {
          const data = JSON.parse(stored);
          if (data.overrides) {
            setUserOverrides(buildOverrideMap(data.overrides));
          }
          return;
        }

        // No localStorage, fetch fresh from API
        const response = await getOverrides(subscriptionId);
        setUserOverrides(buildOverrideMap(response.overrides));
        
        // Save to localStorage
        localStorage.setItem(storageKey, JSON.stringify({
          overrides: response.overrides,
          timestamp: new Date().toISOString(),
        }));
      } catch (error) {
        console.error("Failed to load overrides:", error);
      }
    };

    loadFromStorage();
  }, [subscriptionId]);

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

  const userOverrideNameMap = useMemo(() => {
    const map = new Map<string, string>();

    if (graphData?.node_overrides) {
      Object.entries(graphData.node_overrides).forEach(([nodeId, override]) => {
        const overrideName = (override as any)?.name as string | undefined;
        if (!overrideName) return;
        const key = String(nodeId).toLowerCase();
        if (!key) return;
        map.set(key, overrideName);
      });
    }

    (graphData?.nodes ?? []).forEach(node => {
      const overrideName = (node?.metadata as any)?.user_override?.name as string | undefined;
      if (!overrideName) return;
      const key = String(node.id).toLowerCase();
      if (!key) return;
      map.set(key, overrideName);
    });
    return map;
  }, [graphData?.node_overrides, graphData?.nodes]);

  const getEffectiveResourceName = useCallback((resourceId: string, fallbackName: string) => {
    const key = String(resourceId).toLowerCase();
    return (
      userOverrideNameMap.get(key) ||
      annotationMap.get(resourceId)?.display_name ||
      fallbackName
    );
  }, [annotationMap, userOverrideNameMap]);

  // Helper function to get element weight from graph annotations
  const getElementWeight = useCallback((resourceId: string): number => {
    const annotation = annotationMap.get(resourceId);
    return annotation?.criticality_weight ?? 1.0;
  }, [annotationMap]);

  // Helper function to calculate resilience score for a single resource
  // Uses shared utility - SINGLE SOURCE OF TRUTH
  const calculateResourceScore = useCallback((resourceId: string, checks: any[]): number => {
    const elementWeight = getElementWeight(resourceId);
    const weights: ResiliencyWeights = { categoryWeights, impactWeights };
    return calculateResiliencyScore(checks, elementWeight, weights);
  }, [getElementWeight, categoryWeights, impactWeights]);

  // Map resourceId -> resilience score for use by graph nodes
  const resourceScores = useMemo(() => {
    const scores = new Map<string, number>();
    Object.entries(evaluations).forEach(([resourceId, evaluation]: [string, any]) => {
      const checks = evaluation.checks || evaluation.findings || [];
      const score = calculateResourceScore(resourceId, checks);
      scores.set(resourceId, score);
    });
    return scores;
  }, [evaluations, calculateResourceScore]);

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

  const resourceMatchesFilters = (resourceId: string, evaluation?: any): boolean => {
    // Check viewLevel (importance/layer) filter
    if (viewLevel && graphData?.nodes) {
      const node = graphData.nodes.find(n => String(n.id).toLowerCase() === resourceId.toLowerCase());
      if (node) {
        const maxImportance = LEVEL_TO_MAX_IMPORTANCE[viewLevel];
        const baseImportance = (node.metadata as any)?.importance ?? 3;
        let importance = baseImportance;
        
        // Check AI annotations
        const annotation = annotationMap.get(resourceId);
        if (annotation?.layer !== undefined) {
          importance = annotation.layer;
        }
        
        // Check user overrides
        const nodeOverride = graphData?.node_overrides?.[resourceId];
        if (nodeOverride && typeof (nodeOverride as any)?.layer === "number") {
          importance = (nodeOverride as any).layer;
        }
        
        if (importance > maxImportance) return false;
      }
    }
    
    // If resourceGroupFilter is defined and empty, exclude everything
    if (resourceGroupFilter !== undefined && resourceGroupFilter.size === 0) {
      return false;
    }

    // Check resource group filter if it has items
    if (resourceGroupFilter && resourceGroupFilter.size > 0) {
      // Prefer resource group from evaluation payload if present
      const evalRg = (evaluation?.resource_group || evaluation?.resource_group_name || evaluation?.resourceGroup || "")
        .toString()
        .toLowerCase();

      if (evalRg) {
        if (!resourceGroupFilter.has(evalRg)) return false;
      } else {
        // Fallback: extract from Azure resource ID
        // Format: /subscriptions/sub-id/resourceGroups/group-name/providers/...
        const parts = resourceId.split("/").filter(p => p);
        const partsLower = parts.map(p => p.toLowerCase());
        const rgIndex = partsLower.indexOf("resourcegroups");
        const resourceGroup =
          rgIndex !== -1 && rgIndex + 1 < parts.length
            ? String(parts[rgIndex + 1]).toLowerCase()
            : null;

        if (resourceGroup && !resourceGroupFilter.has(resourceGroup)) {
          return false;
        }
      }
    }

    // If serviceFilter is defined and empty, exclude everything
    if (serviceFilter !== undefined && serviceFilter.size === 0) {
      return false;
    }

    // Check service filter if it has items
    if (serviceFilter && serviceFilter.size > 0) {
      const keyFromGraph = resourceIdToServiceKey.get(resourceId.toLowerCase());
      const keyFromEval = normalizeTypeString(evaluation?.resource_type);
      const serviceKey = String((keyFromGraph ?? keyFromEval ?? "") as string).toLowerCase();
      if (serviceKey && !serviceFilter.has(serviceKey)) return false;
    }

    return true;
  };

  const checkMatchesFilters = (check: any): boolean => {
    // If validationSourceFilter is defined and empty, exclude everything
    if (validationSourceFilter !== undefined && validationSourceFilter.size === 0) {
      return false;
    }

    // Check validation source filter if it has items
    if (validationSourceFilter && validationSourceFilter.size > 0) {
      if (check.validation_source) {
        // Check if the source matches the filter
        if (!validationSourceFilter.has(check.validation_source)) {
          return false;
        }
      } else {
        // If check has no validation_source and filter is active, exclude it
        return false;
      }
    }

    return true;
  };

  const filteredEvaluationEntries = useMemo(() => {
    return Object.entries(evaluations).filter(([resourceId, evaluation]) =>
      resourceMatchesFilters(resourceId, evaluation)
    );
  }, [evaluations, resourceGroupFilter, serviceFilter, resourceIdToServiceKey, viewLevel, graphData, annotationMap]);

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

  const subscriptionNameMap = useMemo(() => {
    const map = new Map<string, string>();
    (subscriptionOptions || []).forEach(sub => {
      if (sub?.id) map.set(sub.id, sub.name || sub.id);
    });
    return map;
  }, [subscriptionOptions]);

  const extractSubscriptionId = (resourceId: string): string | null => {
    const parts = resourceId.split("/").filter(p => p);
    if (parts.length < 2) return null;
    if (parts[0].toLowerCase() !== "subscriptions") return null;
    return parts[1] || null;
  };

  // Apply user overrides to filtered evaluations (used for all display)
  const evaluationsWithOverrides = useMemo(() => {
    return Object.fromEntries(
      filteredEvaluationEntries.map(([resourceId, evaluation]) => {
        const checksOrFindings = (evaluation.findings || evaluation.checks || []).map((finding: any) => {
          // Use resilience_check_id directly from the check object
          const overrideKey = finding.resilience_check_id;
          const override = userOverrides[overrideKey];
          
          if (override) {
            return {
              ...finding,
              status: override.status,
              validation_source: override.validation_source,
            };
          }
          return finding;
        });

        // Apply validation source filter at the check level
        const filteredChecks = checksOrFindings.filter(checkMatchesFilters);
        
        // Recalculate passed/failed counts
        const passed = filteredChecks.filter((f: any) => f.status === "pass").length;
        const failed = filteredChecks.filter((f: any) => f.status === "fail").length;
        
        return [
          resourceId,
          {
            ...evaluation,
            findings: evaluation.findings ? filteredChecks : undefined,
            checks: evaluation.checks ? filteredChecks : undefined,
            passed_checks: passed,
            failed_checks: failed,
            total_checks: filteredChecks.length,
          }
        ];
      })
    );
  }, [filteredEvaluationEntries, userOverrides, validationSourceFilter]);

  const stats = useMemo(() => {
    let totalChecks = 0;
    let passedChecks = 0;
    let failedChecks = 0;
    const resourceList: Array<ResiliencyEvaluation & { resourceId: string }> = [];

    Object.entries(evaluationsWithOverrides).forEach(([resourceId, evaluation]: [string, any]) => {
      // Calculate counts from checks array
      const checks = evaluation.checks || [];
      const evalTotal = checks.length;
      const evalPassed = checks.filter((c: any) => c.status === 'pass').length;
      const evalFailed = checks.filter((c: any) => c.status === 'fail').length;
      
      totalChecks += evalTotal;
      passedChecks += evalPassed;
      failedChecks += evalFailed;
      resourceList.push({ ...evaluation, resourceId });
    });

    const passPercentage = totalChecks > 0 ? passedChecks / totalChecks : 0;

    return { totalChecks, passedChecks, failedChecks, passPercentage, resourceList };
  }, [evaluationsWithOverrides]);

  // Calculate adjusted workload score based on overrides
  // Formula: Check Weight = Element Weight × Category Weight × Impact Weight
  // Normalized Weight = Check Weight / Sum of all Check Weights
  // Score = Sum of normalized weights for passed checks
  const adjustedWorkloadScore = useMemo(() => {
    
    // First pass: calculate total weight for normalization
    let totalWeight = 0;
    Object.entries(evaluationsWithOverrides).forEach(([resourceId, evaluation]: [string, any]) => {
      const elementWeight = getElementWeight(resourceId);
      const checksOrFindings = evaluation.findings || evaluation.checks || [];
      checksOrFindings.forEach((finding: any) => {
        const category = finding.category || "Other";
        const impactWeight = impactWeights[finding.impact] || 0.1;
        const categoryWeight = categoryWeights[category] || 0.05;
        const checkWeight = elementWeight * categoryWeight * impactWeight;
        totalWeight += checkWeight;
      });
    });

    // Second pass: calculate score with normalized weights
    let passedWeight = 0;
    Object.entries(evaluationsWithOverrides).forEach(([resourceId, evaluation]: [string, any]) => {
      const elementWeight = getElementWeight(resourceId);
      const checksOrFindings = evaluation.findings || evaluation.checks || [];
      checksOrFindings.forEach((finding: any) => {
        const category = finding.category || "Other";
        const impactWeight = impactWeights[finding.impact] || 0.1;
        const categoryWeight = categoryWeights[category] || 0.05;
        const rawWeight = elementWeight * categoryWeight * impactWeight;
        const normalizedWeight = totalWeight > 0 ? rawWeight / totalWeight : 0;
        if (finding.status === "pass") {
          passedWeight += normalizedWeight;
        }
      });
    });

    return passedWeight;
  }, [evaluationsWithOverrides, impactWeights, categoryWeights, getElementWeight]);

  const resourceFilterOptions = useMemo(() => {
    const seen = new Set<string>();
    stats.resourceList.forEach(resource => {
      const effectiveName = getEffectiveResourceName(resource.resourceId, resource.resource_name || "");
      const displayName = getResourceDisplayName(resource.resourceId, effectiveName);
      if (displayName) seen.add(displayName);
    });
    return Array.from(seen).sort((a, b) => a.localeCompare(b));
  }, [stats.resourceList, annotationMap]);

  // Breakdown by resilience category - uses contribution % for consistency
  // Formula: Category Score = Sum(passed contribution %) / Sum(all contribution %)
  const categoryBreakdown = useMemo(() => {
    // Re-calculate contribution % here to avoid circular dependency
    
    // First, calculate total weight for all checks (same as totalWeightForDrawerFilters)
    let totalWeight = 0;
    const allChecks: any[] = [];
    
    Object.entries(evaluationsWithOverrides).forEach(([resourceId, evaluation]: [string, any]) => {
      const elementWeight = getElementWeight(resourceId);
      const checksOrFindings = evaluation.findings || evaluation.checks || [];
      checksOrFindings.forEach((finding: any) => {
        const category = finding.category || "Other";
        const impactWeight = impactWeights[finding.impact] || 0.1;
        const categoryWeight = categoryWeights[category] || 0.05;
        const checkWeight = elementWeight * categoryWeight * impactWeight;
        totalWeight += checkWeight;
        allChecks.push({
          ...finding,
          elementWeight,
          category,
          checkWeight,
          normalizedWeight: 0 // will be set in second pass
        });
      });
    });
    
    // Second pass: normalize and calculate contribution %
    allChecks.forEach(check => {
      check.normalizedWeight = totalWeight > 0 ? check.checkWeight / totalWeight : 0;
      check.contribution_percent = check.normalizedWeight * 100;
    });
    
    // Third pass: build category breakdown
    const breakdown = new Map<string, { 
      total: number; 
      passed: number; 
      failed: number; 
      totalContribution: number;
      passedContribution: number;
    }>();
    
    allChecks.forEach((check: any) => {
      const existing = breakdown.get(check.category) || { 
        total: 0, 
        passed: 0, 
        failed: 0, 
        totalContribution: 0, 
        passedContribution: 0 
      };
      
      existing.total++;
      existing.totalContribution += check.contribution_percent;
      
      if (check.status === "pass") {
        existing.passed++;
        existing.passedContribution += check.contribution_percent;
      } else {
        existing.failed++;
      }
      
      breakdown.set(check.category, existing);
    });

    return Array.from(breakdown.entries())
      .map(([name, stats]) => ({
        name,
        ...stats,
        passPercentage: stats.total > 0 ? stats.passed / stats.total : 0,
        // Score = passed contribution % / total contribution % for this category
        resilienceScore: stats.totalContribution > 0 ? stats.passedContribution / stats.totalContribution : 0,
      }))
      .sort((a, b) => b.totalContribution - a.totalContribution);
  }, [evaluationsWithOverrides, impactWeights, categoryWeights, getElementWeight]);

  // Breakdown by impact level
  const impactBreakdown = useMemo(() => {
    const breakdown = new Map<string, { total: number; passed: number; failed: number; totalWeight: number; passedWeight: number }>();
    
    // First pass: calculate total weight
    let totalWeight = 0;
    Object.entries(evaluationsWithOverrides).forEach(([resourceId, evaluation]: [string, any]) => {
      const elementWeight = getElementWeight(resourceId);
      const checksOrFindings = (evaluation as any).findings || (evaluation as any).checks || [];
      checksOrFindings.forEach((finding: any) => {
        const category = finding.category || "Other";
        const impactWeight = impactWeights[finding.impact] || 0.1;
        const categoryWeight = categoryWeights[category] || 0.05;
        const checkWeight = elementWeight * categoryWeight * impactWeight;
        totalWeight += checkWeight;
      });
    });
    
    // Second pass: build breakdown with normalized weights
    Object.entries(evaluationsWithOverrides).forEach(([resourceId, evaluation]: [string, any]) => {
      const elementWeight = getElementWeight(resourceId);
      const checksOrFindings = (evaluation as any).findings || (evaluation as any).checks || [];
      checksOrFindings.forEach((finding: any) => {
        const impact = finding.impact || "Unknown";
        const category = finding.category || "Other";
        const impactWeight = impactWeights[impact] || 0.1;
        const categoryWeight = categoryWeights[category] || 0.05;
        const rawWeight = elementWeight * categoryWeight * impactWeight;
        const normalizedWeight = totalWeight > 0 ? rawWeight / totalWeight : 0;
        const existing = breakdown.get(impact) || { total: 0, passed: 0, failed: 0, totalWeight: 0, passedWeight: 0 };
        existing.total++;
        existing.totalWeight += normalizedWeight;
        if (finding.status === "pass") {
          existing.passed++;
          existing.passedWeight += normalizedWeight;
        } else {
          existing.failed++;
        }
        breakdown.set(impact, existing);
      });
    });

    return Array.from(breakdown.entries())
      .map(([name, stats]) => ({
        name,
        ...stats,
        passPercentage: stats.total > 0 ? stats.passed / stats.total : 0,
        resilienceScore: stats.totalWeight > 0 ? stats.passedWeight / stats.totalWeight : 0,
      }))
      .sort((a, b) => b.totalWeight - a.totalWeight);
  }, [evaluationsWithOverrides, impactWeights, categoryWeights, getElementWeight]);

  const subscriptionBreakdown = useMemo(() => {
    const breakdown = new Map<string, { total: number; passed: number; failed: number; totalWeight: number; passedWeight: number }>();

    // Initialize with all selected subscriptions (even if no evaluations yet)
    (subscriptionOptions ?? []).forEach(subOption => {
      breakdown.set(subOption.id, { total: 0, passed: 0, failed: 0, totalWeight: 0, passedWeight: 0 });
    });

    Object.entries(evaluationsWithOverrides).forEach(([resourceId, evaluation]: [string, any]) => {
      const subId = extractSubscriptionId(resourceId) ?? "Unknown";
      const elementWeight = getElementWeight(resourceId);
      const checksOrFindings = (evaluation as any).findings || (evaluation as any).checks || [];

      checksOrFindings.forEach((finding: any) => {
        const category = finding.category || "Other";
        const impactWeight = impactWeights[finding.impact] || 0.1;
        const categoryWeight = categoryWeights[category] || 0.05;
        const rawWeight = elementWeight * categoryWeight * impactWeight;

        const existing = breakdown.get(subId) || { total: 0, passed: 0, failed: 0, totalWeight: 0, passedWeight: 0 };
        existing.total += 1;
        existing.totalWeight += rawWeight;
        if (finding.status === "pass") {
          existing.passed += 1;
          existing.passedWeight += rawWeight;
        } else {
          existing.failed += 1;
        }
        breakdown.set(subId, existing);
      });
    });

    return Array.from(breakdown.entries())
      .map(([subscriptionKey, stats]) => ({
        id: subscriptionKey,
        name: subscriptionNameMap.get(subscriptionKey) || subscriptionKey,
        ...stats,
        passPercentage: stats.total > 0 ? stats.passed / stats.total : 0,
        resilienceScore: stats.totalWeight > 0 ? stats.passedWeight / stats.totalWeight : 0,
      }))
      .sort((a, b) => b.totalWeight - a.totalWeight || a.name.localeCompare(b.name));
  }, [evaluationsWithOverrides, impactWeights, categoryWeights, getElementWeight, subscriptionNameMap, subscriptionOptions]);

  const showSubscriptionColumn = subscriptionBreakdown.length > 1;

  const subscriptionFilterOptions = useMemo(() => {
    return subscriptionBreakdown.map(item => ({ id: item.id, name: item.name }));
  }, [subscriptionBreakdown]);

  useEffect(() => {
    if (!showSubscriptionColumn && filterSubscription) {
      setFilterSubscription(null);
    }
  }, [showSubscriptionColumn, filterSubscription]);

  const defaultBreakdownAppliedRef = useRef(false);
  const lastSubscriptionIdRef = useRef<string | undefined | null>(null);
  const lastSubscriptionCountRef = useRef(0);

  // Reset the default breakdown flag when workload (subscriptionId) changes
  useEffect(() => {
    if (lastSubscriptionIdRef.current !== subscriptionId) {
      lastSubscriptionIdRef.current = subscriptionId;
      defaultBreakdownAppliedRef.current = false;
    }
  }, [subscriptionId]);

  // Apply subscription breakdown when subscriptionOptions count changes
  useEffect(() => {
    const currentCount = (subscriptionOptions ?? []).length;
    
    // Only run if the COUNT changes, not if the object reference changes
    if (lastSubscriptionCountRef.current !== currentCount) {
      lastSubscriptionCountRef.current = currentCount;
      
      if (currentCount > 1) {
        // Multiple subscriptions: use subscription view if not already
        setBreakdownView(prev => prev !== "subscription" ? "subscription" : prev);
      } else {
        // Single subscription: use category view if not already
        setBreakdownView(prev => prev === "subscription" ? "category" : prev);
      }
    }
  }, [subscriptionOptions?.length]);

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
    const breakdown = new Map<string, { total: number; passed: number; failed: number; totalWeight: number; passedWeight: number }>();
    
    // First pass: calculate total weight
    let totalWeight = 0;
    Object.entries(evaluationsWithOverrides).forEach(([resourceId, evaluation]: [string, any]) => {
      const elementWeight = getElementWeight(resourceId);
      const checksOrFindings = (evaluation as any).findings || (evaluation as any).checks || [];
      checksOrFindings.forEach((finding: any) => {
        const category = finding.category || "Other";
        const impactWeight = impactWeights[finding.impact] || 0.1;
        const categoryWeight = categoryWeights[category] || 0.05;
        const checkWeight = elementWeight * categoryWeight * impactWeight;
        totalWeight += checkWeight;
      });
    });
    
    // Second pass: build breakdown with normalized weights
    Object.entries(evaluationsWithOverrides).forEach(([resourceId, evaluation]: [string, any]) => {
      const elementWeight = getElementWeight(resourceId);
      const annotation = annotationMap.get(resourceId);
      const serviceCategory = annotation?.azure_service_category || "Other";
      
      const checksOrFindings = (evaluation as any).findings || (evaluation as any).checks || [];
      checksOrFindings.forEach((finding: any) => {
        const category = finding.category || "Other";
        const impactWeight = impactWeights[finding.impact] || 0.1;
        const categoryWeight = categoryWeights[category] || 0.05;
        const rawWeight = elementWeight * categoryWeight * impactWeight;
        const normalizedWeight = totalWeight > 0 ? rawWeight / totalWeight : 0;
        const existing = breakdown.get(serviceCategory) || { total: 0, passed: 0, failed: 0, totalWeight: 0, passedWeight: 0 };
        existing.total++;
        existing.totalWeight += normalizedWeight;
        if (finding.status === "pass") {
          existing.passed++;
          existing.passedWeight += normalizedWeight;
        } else {
          existing.failed++;
        }
        breakdown.set(serviceCategory, existing);
      });
    });

    return Array.from(breakdown.entries())
      .map(([name, stats]) => ({
        name,
        ...stats,
        passPercentage: stats.total > 0 ? stats.passed / stats.total : 0,
        resilienceScore: stats.totalWeight > 0 ? stats.passedWeight / stats.totalWeight : 0,
      }))
      .sort((a, b) => b.totalWeight - a.totalWeight);
  }, [evaluationsWithOverrides, annotationMap, impactWeights, categoryWeights, getElementWeight]);

  // Get unique validation sources from data
  const validationSources = useMemo(() => {
    const sources = new Set<string>();
    Object.entries(evaluationsWithOverrides).forEach(([, evaluation]) => {
      const checksOrFindings = (evaluation as any).findings || (evaluation as any).checks || [];
      checksOrFindings.forEach((finding: any) => {
        if (finding.validation_source) {
          // Handle both string (legacy) and array (new format)
          const sourceList = Array.isArray(finding.validation_source) 
            ? finding.validation_source 
            : [finding.validation_source];
          sourceList.forEach((src: string) => {
            const val = (src || "").toLowerCase() === "user" ? "User" : src;
            sources.add(val);
          });
        }
      });
    });
    return Array.from(sources).sort();
  }, [evaluationsWithOverrides]);

  const filteredFindings = useMemo(() => {
    const normalizedResourceFilter = resourceFilter.trim().toLowerCase();
    
    // evaluationsWithOverrides already has overrides and filters applied
    let findings = Object.entries(evaluationsWithOverrides).flatMap(([resourceId, evaluation]) => {
      const checksOrFindings = (evaluation as any).findings || (evaluation as any).checks || [];
      return checksOrFindings
        .map((f: any) => {
          // Normalize validation_source display
          const valSource = (f.validation_source || "").toLowerCase() === "user" ? "User" : f.validation_source || "";
          
          return {
            ...f,
            validation_source: valSource,
            resourceId,
            resourceName: evaluation.resource_name,
          };
        })
        // Apply sidebar validation source filter first
        .filter((f: any) => {
          if (validationSourceFilter === undefined || validationSourceFilter.size === 0) {
            return true;
          }
          if (validationSourceFilter.size > 0) {
            const source = f.validation_source || "";
            return validationSourceFilter.has(source);
          }
          return true;
        })
        .filter((f: any) => filterStatus === "all" || (filterStatus === "pending" ? f.status === "pending" : f.status === filterStatus))
        .filter((f: any) => {
          if (!showSubscriptionColumn || !filterSubscription) return true;
          const subId = extractSubscriptionId(f.resourceId) || "Unknown";
          return subId === filterSubscription;
        })
        .filter((f: any) => !filterCategory || f.category === filterCategory)
        .filter((f: any) => !filterImpact || f.impact === filterImpact)
        .filter((f: any) => !filterValidationSource || f.validation_source.includes(filterValidationSource));
    });

    if (normalizedResourceFilter) {
      findings = findings.filter(finding => {
        const effectiveName = getEffectiveResourceName(finding.resourceId, finding.resourceName || "");
        const resourceLabel = getResourceDisplayName(finding.resourceId, effectiveName);
        return resourceLabel.toLowerCase().includes(normalizedResourceFilter);
      });
    }

    // Sorting
    findings.sort((a, b) => {
      let aVal: any = "";
      let bVal: any = "";

      switch (sortColumn) {
        case "subscription":
          aVal = subscriptionNameMap.get(extractSubscriptionId(a.resourceId) || "") || extractSubscriptionId(a.resourceId) || "";
          bVal = subscriptionNameMap.get(extractSubscriptionId(b.resourceId) || "") || extractSubscriptionId(b.resourceId) || "";
          break;
        case "resource":
          aVal = getEffectiveResourceName(a.resourceId, a.resourceName || "");
          bVal = getEffectiveResourceName(b.resourceId, b.resourceName || "");
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
        case "weight":
          // Calculate weight on the fly: element_weight × category_weight × impact_weight
          const getResourceWeight = (finding: any) => {
            const resourceId = Object.keys(evaluationsWithOverrides).find(key => {
              const evaluation = evaluationsWithOverrides[key];
              const checks = (evaluation as any).findings || (evaluation as any).checks || [];
              return checks.some((c: any) => c.recommendation_id === finding.recommendation_id);
            });
            const elementWeight = resourceId ? getElementWeight(resourceId) : 1.0;
            const category = finding.category || "Other";
            const impactWeight = impactWeights[finding.impact] || 0.1;
            const categoryWeight = categoryWeights[category] || 0.05;
            return elementWeight * categoryWeight * impactWeight;
          };
          aVal = getResourceWeight(a);
          bVal = getResourceWeight(b);
          break;
        case "status":
          aVal = a.status === "pass" ? 1 : 0;
          bVal = b.status === "pass" ? 1 : 0;
          break;
        case "validated_by":
          aVal = a.validation_source || "";
          bVal = b.validation_source || "";
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
  }, [evaluationsWithOverrides, filterStatus, filterCategory, filterImpact, filterValidationSource, filterSubscription, showSubscriptionColumn, resourceFilter, sortColumn, sortDirection, annotationMap, getElementWeight, impactWeights, categoryWeights, subscriptionNameMap, validationSourceFilter, viewLevel, serviceFilter, resourceGroupFilter, graphData]);

  // Calculate total weight based ONLY on left-side drawer filters (resource group, service)
  // NOT affected by right-side "Findings Details" filters (status, category, impact, validation_source)
  const totalWeightForDrawerFilters = useMemo(() => {
    let total = 0;

    filteredEvaluationEntries.forEach(([resourceId, evaluation]: [string, any]) => {
      const elementWeight = getElementWeight(resourceId);
      const checksOrFindings = (evaluation as any).findings || (evaluation as any).checks || [];
      // Respect validation source filter when computing weights
      const visibleChecks = checksOrFindings.filter(checkMatchesFilters);

      visibleChecks.forEach((finding: any) => {
        const category = finding.category || "Other";
        const impactWeight = impactWeights[finding.impact] || 0.1;
        const categoryWeight = categoryWeights[category] || 0.05;
        total += elementWeight * categoryWeight * impactWeight;
      });
    });

    return total;
  }, [filteredEvaluationEntries, impactWeights, categoryWeights, getElementWeight, validationSourceFilter]);

  // Calculate contribution_percent dynamically based on visible filtered checks
  const findingsWithContribution = useMemo(() => {
    // Use totalWeightForDrawerFilters calculated from left-side drawer filters only
    // This way, the weight doesn't change when using right-side "Findings Details" filters

    // Add dynamic contribution_percent to each finding and apply user overrides
    
    return filteredFindings.map(finding => {
      const resourceId = Object.keys(evaluationsWithOverrides).find(key => {
        const evaluation = evaluationsWithOverrides[key];
        const checks = (evaluation as any).findings || (evaluation as any).checks || [];
        return checks.some((c: any) => c.recommendation_id === finding.recommendation_id);
      });
      const elementWeight = resourceId ? getElementWeight(resourceId) : 1.0;
      const category = finding.category || "Other";
      const impactWeight = impactWeights[finding.impact] || 0.1;
      const categoryWeight = categoryWeights[category] || 0.05;
      const rawWeight = elementWeight * categoryWeight * impactWeight;
      const checkWeight = totalWeightForDrawerFilters > 0 ? rawWeight / totalWeightForDrawerFilters : 0;
      
      return {
        ...finding,
        contribution_percent: checkWeight * 100  // Already normalized, just convert to percentage
      };
    });
  }, [filteredFindings, totalWeightForDrawerFilters, evaluationsWithOverrides, impactWeights, categoryWeights, getElementWeight]);

  // Group findings by recommendation
  const groupedRecommendations = useMemo(() => {
    const groups = new Map<string, {
      recommendation_id: string;
      description: string;
      category: string;
      impact: string;
      potential_benefits: string;
      learn_more: any;
      resources: Array<any>;
      totalContribution: number;
      failedCount: number;
      passedCount: number;
      pendingCount: number;
    }>();

    findingsWithContribution.forEach(finding => {
      const key = finding.recommendation_id;
      if (!groups.has(key)) {
        groups.set(key, {
          recommendation_id: finding.recommendation_id,
          description: finding.description,
          category: finding.category,
          impact: finding.impact,
          potential_benefits: finding.potential_benefits,
          learn_more: finding.learn_more,
          resources: [],
          totalContribution: 0,
          failedCount: 0,
          passedCount: 0,
          pendingCount: 0,
        });
      }

      const group = groups.get(key)!;
      group.resources.push(finding);
      group.totalContribution += finding.contribution_percent || 0;
      
      if (finding.status === "fail") group.failedCount++;
      else if (finding.status === "pass") group.passedCount++;
      else if (finding.status === "pending") group.pendingCount++;
    });

    // Convert to array and sort
    return Array.from(groups.values()).sort((a, b) => {
      if (sortColumn === "weight") {
        return sortDirection === "asc" 
          ? a.totalContribution - b.totalContribution
          : b.totalContribution - a.totalContribution;
      }
      if (sortColumn === "recommendation") {
        return sortDirection === "asc"
          ? a.description.localeCompare(b.description)
          : b.description.localeCompare(a.description);
      }
      if (sortColumn === "category") {
        return sortDirection === "asc"
          ? a.category.localeCompare(b.category)
          : b.category.localeCompare(a.category);
      }
      if (sortColumn === "impact") {
        const impactOrder = { "High": 3, "Medium": 2, "Low": 1 };
        const aVal = impactOrder[a.impact as keyof typeof impactOrder] || 0;
        const bVal = impactOrder[b.impact as keyof typeof impactOrder] || 0;
        return sortDirection === "asc" ? aVal - bVal : bVal - aVal;
      }
      return 0;
    });
  }, [findingsWithContribution, sortColumn, sortDirection]);

  useEffect(() => {
    if (!highlightRecommendationId && !highlightRecommendationTitle) return;

    setFilterStatus("all");
    setFilterCategory(null);
    setFilterImpact(null);
    setFilterValidationSource(null);
    setFilterSubscription(null);
    setResourceFilter("");
  }, [highlightRecommendationId, highlightRecommendationTitle]);

  useEffect(() => {
    if (!highlightRecommendationId && !highlightRecommendationTitle) return;

    const target = groupedRecommendations.find(group =>
      isHighlightedRecommendation(group.recommendation_id, group.description)
    );
    if (!target) return;

    const element = document.getElementById(getRecommendationRowId(target.recommendation_id));
    if (!element) return;

    element.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [highlightRecommendationId, highlightRecommendationTitle, groupedRecommendations]);

  // Handler to open resource modal
  const handleResourceClick = (finding: any) => {
    onResourceSelect?.(finding.resourceId);
  };

  const handleStatusOverride = async (
    resourceId: string,
    recommendationId: string,
    resilienceCheckId: string,
    currentStatus: string
  ) => {
    const targetSubscriptionId = subscriptionId ?? extractSubscriptionId(resourceId);
    if (!targetSubscriptionId) {
      console.error("Cannot save override: subscription ID not available");
      return;
    }

    // Toggle logic: fail→pass, pending→pass, pass→fail
    const newStatus: "pass" | "fail" = currentStatus === "fail" || currentStatus === "pending" ? "pass" : "fail";
    
    // Optimistically update UI using resilience_check_id as key
    setUserOverrides(prev => {
      const updated = {
        ...prev,
        [resilienceCheckId]: {
          status: newStatus,
          validation_source: "User",
          resilience_check_id: resilienceCheckId,
        },
      };
      return updated;
    });

    // Save to backend
    try {
      const saved = await saveOverride(
        targetSubscriptionId,
        resourceId,
        recommendationId,
        newStatus,
        "user"
      );

      // Prefer check_uuid from backend (authoritative). Fallback to the incoming id if not present.
      const savedCheckId = (saved as any)?.check_uuid || resilienceCheckId;
      if (!savedCheckId) {
        console.warn("Override saved but no resilience_check_id available; skipping local state update.");
        return;
      }

      // Update local state with backend confirmation
      setUserOverrides(prev => {
        const updated = {
          ...prev,
          [savedCheckId]: {
            status: newStatus as "pass" | "fail" | "pending",
            validation_source: "User",
            resilience_check_id: savedCheckId,
          },
        };
        
        // Save to localStorage
        if (targetSubscriptionId) {
          const storageKey = `resilience_${targetSubscriptionId}`;
          const stored = localStorage.getItem(storageKey) || '{}';
          const data = JSON.parse(stored);
          data.overrides = data.overrides || {};
          data.overrides[savedCheckId] = {
            resource_id: resourceId,
            recommendation_id: recommendationId,
            status: newStatus,
            overridden_by: "user",
            resilience_check_id: savedCheckId,
          };
          data.timestamp = new Date().toISOString();
          localStorage.setItem(storageKey, JSON.stringify(data));
        }
        
        return updated;
      });
      
      onOverrideSaved?.({
        resource_id: resourceId,
        recommendation_id: recommendationId,
        status: newStatus,
        overridden_by: "user",
        resilience_check_id: savedCheckId,
      });
    } catch (error) {
      console.error("Failed to save override:", error);
      // Revert optimistic update on error
      setUserOverrides(prev => {
        const updated = { ...prev };
        delete updated[resilienceCheckId];
        return updated;
      });
      alert("Failed to save override. Please try again.");
    }
  };

  const handleDeleteOverride = async (
    resourceId: string,
    recommendationId: string,
    resilienceCheckId: string
  ) => {
    const targetSubscriptionId = subscriptionId ?? extractSubscriptionId(resourceId);
    if (!targetSubscriptionId) return;
    
    try {
      if (!resilienceCheckId) {
        console.warn("deleteOverride: resilience_check_id missing; aborting delete request.");
        return;
      }

      await deleteOverride(targetSubscriptionId, resilienceCheckId);
      
      // Update local state immediately by removing the override
      setUserOverrides(prev => {
        const updated = { ...prev };
        delete updated[resilienceCheckId];
        return updated;
      });
      
      // Update localStorage after state update
      if (targetSubscriptionId) {
        const storageKey = `resilience_${targetSubscriptionId}`;
        const stored = localStorage.getItem(storageKey) || '{}';
        const data = JSON.parse(stored);
        if (data.overrides && resilienceCheckId in data.overrides) {
          delete data.overrides[resilienceCheckId];
        }
        data.timestamp = new Date().toISOString();
        localStorage.setItem(storageKey, JSON.stringify(data));
      }

      onOverrideDeleted?.(resilienceCheckId, resourceId);
    } catch (e) {
      console.error("deleteOverride failed", e);
      alert("Failed to delete override. Please try again.");
    }
  };

  const handleBatchOverride = async (
    resources: Array<any>,
    newStatus: "pass" | "fail"
  ) => {
    if (resources.length === 0) return;
    
    const targetSubscriptionId = subscriptionId ?? extractSubscriptionId(resources[0].resourceId);
    if (!targetSubscriptionId) {
      console.error("Cannot save batch override: subscription ID not available");
      return;
    }

    // Prepare batch items
    const items: BatchOverrideItem[] = resources.map(resource => ({
      resource_id: resource.resourceId,
      recommendation_id: resource.recommendation_id,
      resilience_check_id: resource.resilience_check_id,
    }));

    // Optimistically update UI for all items
    const optimisticUpdates: Record<string, any> = {};
    items.forEach(item => {
      optimisticUpdates[item.resilience_check_id] = {
        status: newStatus,
        validation_source: "User",
        resilience_check_id: item.resilience_check_id,
      };
    });

    setUserOverrides(prev => ({
      ...prev,
      ...optimisticUpdates,
    }));

    try {
      const result = await saveBatchOverrides(
        targetSubscriptionId,
        items,
        newStatus,
        "user"
      );

      // Update local state with backend confirmation
      const confirmedUpdates: Record<string, any> = {};
      result.overrides.forEach((override: any) => {
        const savedCheckId = override?.check_uuid || override?.resilience_check_id;
        if (savedCheckId) {
          confirmedUpdates[savedCheckId] = {
            status: newStatus as "pass" | "fail" | "pending",
            validation_source: "User",
            resilience_check_id: savedCheckId,
          };
        }
      });

      setUserOverrides(prev => ({
        ...prev,
        ...confirmedUpdates,
      }));

      // Save to localStorage
      if (targetSubscriptionId) {
        const storageKey = `resilience_${targetSubscriptionId}`;
        const stored = localStorage.getItem(storageKey) || '{}';
        const data = JSON.parse(stored);
        data.overrides = data.overrides || {};
        
        result.overrides.forEach((override: any, idx: number) => {
          const savedCheckId = override?.check_uuid || override?.resilience_check_id;
          if (savedCheckId) {
            data.overrides[savedCheckId] = {
              resource_id: items[idx].resource_id,
              recommendation_id: items[idx].recommendation_id,
              status: newStatus,
              overridden_by: "user",
              resilience_check_id: savedCheckId,
            };
          }
        });
        
        data.timestamp = new Date().toISOString();
        localStorage.setItem(storageKey, JSON.stringify(data));
      }

     // Call onOverrideSaved for each item
      result.overrides.forEach((override: any, idx: number) => {
        const savedCheckId = override?.check_uuid || override?.resilience_check_id;
        if (savedCheckId && onOverrideSaved) {
          onOverrideSaved({
            resource_id: items[idx].resource_id,
            recommendation_id: items[idx].recommendation_id,
            status: newStatus,
            overridden_by: "user",
            resilience_check_id: savedCheckId,
          });
        }
      });
    } catch (error) {
      console.error("Failed to save batch overrides:", error);
      // Revert optimistic updates on error
      setUserOverrides(prev => {
        const updated = { ...prev };
        items.forEach(item => {
          delete updated[item.resilience_check_id];
        });
        return updated;
      });
      alert("Failed to save batch overrides. Please try again.");
    }
  };

  const handleBatchDeleteOverrides = async (
    resources: Array<any>,
  ) => {
    if (resources.length === 0) return;
    
    const targetSubscriptionId = subscriptionId ?? extractSubscriptionId(resources[0].resourceId);
    if (!targetSubscriptionId) {
      console.error("Cannot delete batch overrides: subscription ID not available");
      return;
    }

    // Optimistically update UI for all items
    const optimisticAdditions: Record<string, any> = {};
    resources.forEach(resource => {
      optimisticAdditions[resource.resilience_check_id] = {
        status: "deleted",  // Mark as deleted locally first
        validation_source: resource.validation_source,
        resilience_check_id: resource.resilience_check_id,
      };
    });

    setUserOverrides(prev => {
      const updated = { ...prev };
      resources.forEach(resource => {
        delete updated[resource.resilience_check_id];
      });
      return updated;
    });

    try {
      // Delete each override individually
      for (const resource of resources) {
        if (resource.resilience_check_id) {
          await deleteOverride(targetSubscriptionId, resource.resilience_check_id);
        }
      }

      // Update localStorage after all deletions
      if (targetSubscriptionId) {
        const storageKey = `resilience_${targetSubscriptionId}`;
        const stored = localStorage.getItem(storageKey) || '{}';
        const data = JSON.parse(stored);
        if (data.overrides) {
          resources.forEach(resource => {
            if (resource.resilience_check_id in data.overrides) {
              delete data.overrides[resource.resilience_check_id];
            }
          });
        }
        data.timestamp = new Date().toISOString();
        localStorage.setItem(storageKey, JSON.stringify(data));
      }

      // Call onOverrideDeleted for each deleted item
      resources.forEach(resource => {
        onOverrideDeleted?.(resource.resilience_check_id, resource.resourceId);
      });
    } catch (e) {
      console.error("Failed to delete batch overrides:", e);
      // Revert optimistic updates on error - need to refetch
      alert("Failed to delete overrides. Please try again.");
    }
  };

  const getStatusColor = (status: string) => {
    if (status === "pass") return "#10b981";
    if (status === "pending") return "#6b7280";
    return "#ef4444";
  };

  const exportToExcel = () => {
    // Prepare data for export
    const exportData = findingsWithContribution.map(finding => ({
      "Resource": annotationMap.get(
        Object.keys(evaluationsWithOverrides).find(key => {
          const evaluation = evaluationsWithOverrides[key];
          const checks = (evaluation as any).findings || (evaluation as any).checks || [];
          return checks.some((c: any) => c.recommendation_id === finding.recommendation_id);
        }) || ""
      )?.display_name || finding.description?.substring(0, 30) || "Unknown",
      "Recommendation": finding.description,
      "Category": finding.category,
      "Impact": finding.impact,
      "Contribution %": finding.contribution_percent?.toFixed(2) || "0.00",
      "Status": finding.status === "pass" ? "Passed" : "Failed",
      "Validated By": finding.validation_source || "APRL",
      "Benefit": finding.potential_benefits || ""
    }));

    // Create workbook and worksheet
    const wb = XLSX.utils.book_new();
    const ws = XLSX.utils.json_to_sheet(exportData);

    // Set column widths
    const colWidths = [
      { wch: 20 },  // Resource
      { wch: 40 },  // Recommendation
      { wch: 18 },  // Category
      { wch: 10 },  // Impact
      { wch: 15 },  // Contribution %
      { wch: 10 },  // Status
      { wch: 15 },  // Validated By
      { wch: 30 },  // Benefit
    ];
    ws["!cols"] = colWidths;

    // Add worksheet to workbook
    XLSX.utils.book_append_sheet(wb, ws, "Findings");

    // Generate filename with timestamp
    const timestamp = new Date().toISOString().slice(0, 10);
    const filename = `resilience-findings-${timestamp}.xlsx`;

    // Write file
    XLSX.writeFile(wb, filename);
  };
  const getRiskLevel = (passPercentage: number) => {
    const percentage = passPercentage * 100;  // Convert 0-1 to 0-100
    if (percentage >= 80) return { level: "Low Risk", color: "#10b981", badge: "✓" };
    if (percentage >= 60) return { level: "Medium Risk", color: "#f97316", badge: "!" };
    if (percentage >= 40) return { level: "High Risk", color: "#ef4444", badge: "⚠" };
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
    <div style={{ padding: "24px", paddingBottom: "64px", background: "#f9fafb", display: "flex", flexDirection: "column" }}>
      <style>{`
        .tooltip-trigger:hover + .tooltip-content {
          opacity: 1 !important;
          visibility: visible !important;
        }
      `}</style>
      {/* Header Section - Overview Card */}
      <div
        style={{
          background: "#fff",
          borderRadius: "12px",
          padding: "16px",
          marginBottom: "24px",
          boxShadow: "0 1px 3px rgba(0,0,0,0.1)",
          border: "1px solid #e5e7eb",
          flexShrink: 0,
        }}
      >
          {/* Main Stats + Category + Impact bars */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "12px"}}>
              <div
                style={{
                  padding: "10px",
                  background: (() => {
                    const score = adjustedWorkloadScore * 100;
                    if (score >= 80) return "#d0fbe7"; // Green
                    if (score >= 60) return "#fef3c7"; // Yellow
                    if (score >= 40) return "#fed7aa"; // Orange
                    return "#fecaca"; // Red
                  })(),
                  borderRadius: "8px",
                  borderLeft: `3px solid ${(() => {
                    const score = adjustedWorkloadScore * 100;
                    if (score >= 80) return "#10b981"; // Green
                    if (score >= 60) return "#f59e0b"; // Orange/Yellow
                    if (score >= 40) return "#f97316"; // Dark Orange
                    return "#dc2626"; // Red
                  })()}`,
                }}
              >
                <div style={{ fontSize: "11px", color: "#5a606b", marginBottom: "4px" }}>Overall Score</div>
                <div style={{ fontSize: "18px", fontWeight: 700, color: (() => {
                  const score = adjustedWorkloadScore * 100;
                  if (score >= 80) return "#10b981"; // Green
                  if (score >= 60) return "#f59e0b"; // Orange/Yellow
                  if (score >= 40) return "#f97316"; // Dark Orange
                  return "#dc2626"; // Red
                })() }}>
                  {(adjustedWorkloadScore * 100).toFixed(0)}%
                </div>
              </div>

              <div
                style={{
                  padding: "10px",
                  background: "#dee7f3",
                  borderRadius: "8px",
                  borderLeft: "3px solid #448eef",
                }}
              >
                <div style={{ fontSize: "11px", color: "#5a606b", marginBottom: "4px" }}>Total Resiliency Items</div>
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
          {subscriptionBreakdown.length > 1 && (
            <button
              onClick={() => setBreakdownView("subscription")}
              style={{
                padding: "12px 20px",
                border: "none",
                background: breakdownView === "subscription" ? "#fff" : "transparent",
                color: breakdownView === "subscription" ? "#0078d4" : "#6b7280",
                cursor: "pointer",
                fontSize: "13px",
                fontWeight: breakdownView === "subscription" ? 600 : 500,
                borderBottom: breakdownView === "subscription" ? "3px solid #0078d4" : "none",
                marginBottom: "-2px",
                transition: "all 0.2s ease",
              }}
            >
              By Subscription
            </button>
          )}
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
            By Resiliency Category
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
                    justifyContent: "center",
                    marginBottom: "12px",
                  }}
                >
                  <ScoreDonut score={item.resilienceScore} size={80} />
                </div>

                <div style={{ marginTop: "8px" }}>
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
                    justifyContent: "center",
                    marginBottom: "12px",
                  }}
                >
                  <ScoreDonut score={item.resilienceScore} size={80} />
                </div>

                <div style={{ marginTop: "8px" }}>
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
                    justifyContent: "center",
                    marginBottom: "12px",
                  }}
                >
                  <ScoreDonut score={item.resilienceScore} size={80} />
                </div>

                <div style={{ marginTop: "8px" }}>
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

          {breakdownView === "subscription" &&
            subscriptionBreakdown.map((item) => (
              <div
                key={item.id}
                style={{
                  background: "#fff",
                  borderRadius: "6px",
                  padding: "12px",
                  boxShadow: "0 1px 2px rgba(0,0,0,0.08)",
                  border: "1px solid #e5e7eb",
                }}
              >
                <div
                  style={{
                    fontSize: "12px",
                    fontWeight: 600,
                    color: "#1f2937",
                    marginBottom: "10px",
                    wordBreak: "break-word",
                  }}
                >
                  {item.name}
                </div>

                <div
                  style={{
                    display: "flex",
                    justifyContent: "center",
                    marginBottom: "12px",
                  }}
                >
                  <ScoreDonut score={item.resilienceScore} size={80} />
                </div>

                <div style={{ marginTop: "8px" }}>
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
          flex: 1,
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
        }}
      >
        {/* Filter Buttons */}
        <div style={{ marginBottom: "8px", display: "flex", flexWrap: "wrap", gap: "8px", alignItems: "flex-end", justifyContent: "space-between", flexShrink: 0 }}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", alignItems: "flex-end" }}>
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
            onClick={() => setFilterStatus("pending")}
            style={{
              padding: "6px 16px",
              borderRadius: "6px",
              border: filterStatus === "pending" ? "2px solid #9ca3af" : "1px solid #d1d5db",
              background: filterStatus === "pending" ? "#f3f4f6" : "#fff",
              color: filterStatus === "pending" ? "#4b5563" : "#6b7280",
              cursor: "pointer",
              fontSize: "12px",
              fontWeight: 600,
            }}
          >
            Pending
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

          {showSubscriptionColumn && (
            <label style={{ fontSize: "12px", fontWeight: 600, color: "#1f2937", display: "flex", flexDirection: "column", gap: "8px", marginLeft: "10px" }}>
              Subscription
              <select
                value={filterSubscription ?? ""}
                onChange={(e) => setFilterSubscription(e.target.value || null)}
                style={{ padding: "6px 8px", borderRadius: "4px", border: "1px solid #d1d5db", fontSize: "12px", minWidth: "180px" }}
              >
                <option value="">All</option>
                {subscriptionFilterOptions.map(sub => (
                  <option key={sub.id} value={sub.id}>
                    {sub.name}
                  </option>
                ))}
              </select>
            </label>
          )}
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
          <label style={{ fontSize: "12px", fontWeight: 600, color: "#1f2937", display: "flex", flexDirection: "column", gap: "8px", marginLeft: "10px" }}>
            Validated By
            <select
              value={filterValidationSource ?? ""}
              onChange={(e) => setFilterValidationSource(e.target.value || null)}
              style={{ padding: "6px 8px", borderRadius: "4px", border: "1px solid #d1d5db", fontSize: "12px" }}
            >
              <option value="">All</option>
              {validationSources.map(source => (
                <option key={source} value={source}>
                  {source}
                </option>
              ))}
            </select>
          </label>
          </div>
          
          {/* Export Button */}
          <button
            onClick={exportToExcel}
            style={{
              padding: "8px 16px",
              borderRadius: "6px",
              border: "1px solid #d1d5db",
              background: "#fff",
              color: "#0078d4",
              cursor: "pointer",
              fontSize: "12px",
              fontWeight: 600,
              display: "flex",
              alignItems: "center",
              gap: "6px",
              transition: "all 0.2s",
              height: "fit-content",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "#e0f2fe";
              e.currentTarget.style.borderColor = "#0078d4";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "#fff";
              e.currentTarget.style.borderColor = "#d1d5db";
            }}
          >
            <span>📥</span>
            Export to Excel
          </button>
        </div>

        {/* Findings Table */}
        <div
          style={{
            border: "1px solid #e5e7eb",
            borderRadius: "8px",
            flex: 1,
            minHeight: 0,
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
                {showSubscriptionColumn && (
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
                      onClick={() => toggleSort("subscription")}
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
                      <span>Subscription</span>
                      <span style={{ fontSize: "10px", color: "#6b7280" }}>{sortIndicator("subscription")}</span>
                    </button>
                  </th>
                )}
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
                    fontSize: "12px",
                    color: "#374151",
                  }}
                >
                  <button
                    type="button"
                    onClick={() => toggleSort("weight")}
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
                    title="Percentage contribution to current filtered view (recalculated dynamically)"
                  >
                    <span>Weight</span>
                    <span style={{ fontSize: "10px", color: "#6b7280" }}>{sortIndicator("weight")}</span>
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
                <th
                  style={{
                    padding: "12px",
                    textAlign: "center",
                    fontWeight: 600,
                    fontSize: "12px",
                    color: "#374151",
                  }}
                >
                  <button
                    type="button"
                    onClick={() => toggleSort("validated_by")}
                    style={{
                      background: "none",
                      border: "none",
                      cursor: "pointer",
                      display: "flex",
                      alignItems: "center",
                      gap: "4px",
                      fontSize: "12px",
                      fontWeight: 600,
                      color: "#374151",
                      margin: "0 auto",
                    }}
                  >
                    <span>Validated By</span>
                    <span style={{ fontSize: "10px", color: "#6b7280" }}>{sortIndicator("validated_by")}</span>
                  </button>
                </th>
              </tr>
            </thead>
            <tbody>
              {groupedRecommendations.map((group, idx) => (
                <tr
                  key={group.recommendation_id}
                  id={getRecommendationRowId(group.recommendation_id)}
                  style={{
                    borderBottom: "1px solid #e5e7eb",
                    background: isHighlightedRecommendation(group.recommendation_id, group.description)
                      ? "#dbeafe"
                      : idx % 2 === 0
                        ? "#fff"
                        : "#f9fafb",
                  }}
                >
                  {showSubscriptionColumn && (
                    <td style={{ padding: "12px", color: "#374151", fontWeight: 600 }}>
                      {(() => {
                        // Get subscription from first resource
                        const firstResource = group.resources[0];
                        return subscriptionNameMap.get(extractSubscriptionId(firstResource.resourceId) || "")
                          || extractSubscriptionId(firstResource.resourceId)
                          || "Unknown";
                      })()}
                    </td>
                  )}
                  <td style={{ padding: "12px", color: "#374151" }}>
                    <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                      {/* Recommendation Text */}
                      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                        <span style={{ fontWeight: 500 }}>{group.description}</span>
                        {((group.learn_more as any)?.llm_reasoning || (group.learn_more as any)?.heuristic_reasoning) && (() => {
                          const tooltipId = `tooltip-${group.recommendation_id}`;
                          const isTooltipVisible = visibleTooltip === tooltipId;
                          const learnMore = group.learn_more as any;
                          const reasoning = learnMore?.llm_reasoning || learnMore?.heuristic_reasoning;
                          const reasoningType = learnMore?.llm_reasoning ? "LLM Analysis" : "Heuristic Analysis";
                          
                          return (
                            <div
                              style={{
                                display: "inline-flex",
                                alignItems: "center",
                                justifyContent: "center",
                                width: "18px",
                                height: "18px",
                                minWidth: "18px",
                                borderRadius: "50%",
                                background: "#dbeafe",
                                color: "#1e40af",
                                cursor: "help",
                                fontSize: "11px",
                                fontWeight: 700,
                                border: "1px solid #93c5fd",
                                position: "relative",
                              }}
                              onMouseEnter={() => setVisibleTooltip(tooltipId)}
                              onMouseLeave={() => setVisibleTooltip(null)}
                            >
                              ?
                              <div
                                style={{
                                  position: "absolute",
                                  top: "calc(100% + 8px)",
                                  left: "0",
                                  right: "auto",
                                  background: "#0f172a",
                                  color: "#e5e7eb",
                                  padding: "12px 14px",
                                  borderRadius: "10px",
                                  fontSize: "12px",
                                  whiteSpace: "normal",
                                  width: "min(350px, 80vw)",
                                  maxWidth: "80vw",
                                  zIndex: 10000,
                                  opacity: isTooltipVisible ? 1 : 0,
                                  pointerEvents: isTooltipVisible ? "auto" : "none",
                                  transition: "opacity 0.18s ease",
                                  boxShadow: "0 18px 38px -12px rgba(15, 23, 42, 0.45)",
                                  lineHeight: "1.5",
                                  border: "1px solid rgba(148, 163, 184, 0.35)",
                                  textAlign: "left",
                                }}
                              >
                                {learnMore?.name && (
                                  <div style={{ fontWeight: 700, marginBottom: "6px", color: "#fff", fontSize: "13px" }}>
                                    {learnMore.name}
                                  </div>
                                )}
                                {reasoning && (
                                  <>
                                    <div style={{ fontSize: "10px", color: "#94a3b8", marginBottom: "4px", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px" }}>
                                      {reasoningType}
                                    </div>
                                    <div style={{ fontSize: "12px", color: "#e5e7eb" }}>
                                      {reasoning}
                                    </div>
                                  </>
                                )}
                              </div>
                            </div>
                          );
                        })()}
                      </div>
                      {/* Resource Badges */}
                      <div style={{ display: "flex", flexWrap: "wrap", gap: "4px", marginTop: "2px" }}>
                        {group.resources.map((resource, rIdx) => (
                          <button
                            key={rIdx}
                            onClick={() => handleResourceClick(resource)}
                            style={{
                              padding: "2px 8px",
                              borderRadius: "4px",
                              border: "1px solid #d1d5db",
                              background: resource.status === "fail" ? "#fee2e2" : resource.status === "pass" ? "#ecfdf5" : "#f3f4f6",
                              color: resource.status === "fail" ? "#991b1b" : resource.status === "pass" ? "#065f46" : "#4b5563",
                              cursor: "pointer",
                              fontSize: "10px",
                              fontWeight: 500,
                              display: "inline-flex",
                              alignItems: "center",
                              gap: "4px",
                              transition: "all 0.15s ease",
                            }}
                            onMouseEnter={(e) => {
                              e.currentTarget.style.transform = "translateY(-1px)";
                              e.currentTarget.style.boxShadow = "0 2px 4px 0 rgba(0,0,0,0.1)";
                            }}
                            onMouseLeave={(e) => {
                              e.currentTarget.style.transform = "translateY(0)";
                              e.currentTarget.style.boxShadow = "none";
                            }}
                            title={resource.resourceId}
                          >
                            {getResourceDisplayName(
                              resource.resourceId,
                              getEffectiveResourceName(resource.resourceId, resource.resourceName || "")
                            )}
                          </button>
                        ))}
                      </div>
                    </div>
                  </td>
                  <td style={{ padding: "12px", color: "#374151" }}>
                    {group.potential_benefits ? (
                      (group.learn_more as any)?.url ? (
                        <a
                          href={(group.learn_more as any).url}
                          target="_blank"
                          rel="noreferrer"
                          style={{ color: "#2563eb", textDecoration: "none", fontWeight: 600 }}
                          title={(group.learn_more as any).name || "Learn more"}
                        >
                          {group.potential_benefits}
                        </a>
                      ) : (
                        group.potential_benefits
                      )
                    ) : (
                      "—"
                    )}
                  </td>
                  <td style={{ padding: "12px", color: "#374151" }}>
                    {group.category}
                  </td>
                  <td
                    style={{
                      padding: "12px",
                      textAlign: "center",
                      color: getImpactColor(group.impact),
                      fontWeight: 600,
                    }}
                  >
                    {group.impact}
                  </td>
                  <td
                    style={{
                      padding: "12px",
                      textAlign: "center",
                      fontSize: "11px",
                      color: "#6b7280",
                      fontWeight: 500,
                    }}
                    title={`Total contribution: ${group.totalContribution.toFixed(2)}%`}
                  >
                    {group.totalContribution.toFixed(1)}%
                  </td>
                  <td
                    style={{
                      padding: "12px",
                      textAlign: "center",
                    }}
                  >
                    <div style={{ display: "flex", flexDirection: "column", gap: "4px", alignItems: "center" }}>
                      {group.failedCount > 0 && (
                        <div style={{ display: "flex", alignItems: "center", gap: "4px", justifyContent: "center" }}>
                          <span
                            style={{
                              display: "inline-block",
                              padding: "3px 8px",
                              borderRadius: "4px",
                              background: "#fee2e2",
                              color: "#991b1b",
                              fontWeight: 600,
                              fontSize: "10px",
                            }}
                          >
                            {group.failedCount} FAIL
                          </span>
                          <button
                            onClick={() => {
                              const failedResources = group.resources.filter(r => r.status === "fail");
                              handleBatchOverride(failedResources, "pass");
                            }}
                            style={{
                              padding: "2px 2px",
                              borderRadius: "3px",
                              border: "1px solid #10b981",
                              background: "#10b981",
                              cursor: "pointer",
                              display: "inline-flex",
                              alignItems: "center",
                              position: "relative",
                              top: "-10px",
                              left: "-10px", 
                            }}
                            title={`Override all ${group.failedCount} failed to pass`}
                          >
                            <img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'%3E%3Cpath fill='white' d='M362.7 19.3L314.3 67.7 444.3 197.7l48.4-48.4c25-25 25-65.5 0-90.5L453.3 19.3c-25-25-65.5-25-90.5 0zm-71 71L58.6 323.5c-10.4 10.4-18 23.3-22.2 37.4L1 481.2C-1.5 489.7 .8 498.8 7 505s15.3 8.5 23.7 6.1l120.3-35.4c14.1-4.2 27-11.8 37.4-22.2L421.7 220.3 291.7 90.3z'/%3E%3C/svg%3E" alt="Override" style={{ width: "10px", height: "10px" }} />
                          </button>
                        </div>
                      )}
                      {group.passedCount > 0 && (
                        <div style={{ display: "flex", alignItems: "center", gap: "4px", justifyContent: "center" }}>
                          <span
                            style={{
                              display: "inline-block",
                              padding: "3px 8px",
                              borderRadius: "4px",
                              background: "#ecfdf5",
                              color: "#065f46",
                              fontWeight: 600,
                              fontSize: "10px",
                            }}
                          >
                            {group.passedCount} PASS
                          </span>
                          {group.resources.filter(r => r.status === "pass" && r.validation_source?.toLowerCase() === "user").length > 0 && (
                            <button
                              onClick={() => {
                                const userPassedResources = group.resources.filter(r => 
                                  r.status === "pass" && r.validation_source?.toLowerCase() === "user"
                                );
                                handleBatchDeleteOverrides(userPassedResources);
                              }}
                              style={{
                                padding: "2px 2px",
                                borderRadius: "3px",
                                border: "1px solid #ef4444",
                                background: "#ef4444",
                                cursor: "pointer",
                                display: "inline-flex",
                                alignItems: "center",
                                position: "relative",
                                top: "-10px",
                                left: "-10px", 
                              }}
                              title={`Revert ${group.resources.filter(r => r.status === "pass" && r.validation_source?.toLowerCase() === "user").length} user overrides`}
                            >
                              <img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'%3E%3Cpath fill='white' d='M362.7 19.3L314.3 67.7 444.3 197.7l48.4-48.4c25-25 25-65.5 0-90.5L453.3 19.3c-25-25-65.5-25-90.5 0zm-71 71L58.6 323.5c-10.4 10.4-18 23.3-22.2 37.4L1 481.2C-1.5 489.7 .8 498.8 7 505s15.3 8.5 23.7 6.1l120.3-35.4c14.1-4.2 27-11.8 37.4-22.2L421.7 220.3 291.7 90.3z'/%3E%3C/svg%3E" alt="Override" style={{ width: "10px", height: "10px" }} />
                            </button>
                          )}
                        </div>
                      )}
                      {group.pendingCount > 0 && (
                        <div style={{ display: "flex", alignItems: "center", gap: "4px", justifyContent: "center" }}>
                          <span
                            style={{
                              display: "inline-block",
                              padding: "3px 8px",
                              borderRadius: "4px",
                              background: "#f3f4f6",
                              color: "#4b5563",
                              fontWeight: 600,
                              fontSize: "10px",
                            }}
                          >
                            {group.pendingCount} PENDING
                          </span>
                          <button
                            onClick={() => {
                              const pendingResources = group.resources.filter(r => r.status === "pending");
                              handleBatchOverride(pendingResources, "pass");
                            }}
                            style={{
                              padding: "2px 2px",
                              borderRadius: "3px",
                              border: "1px solid #10b981",
                              background: "#10b981",
                              cursor: "pointer",
                              display: "inline-flex",
                              alignItems: "center",
                              position: "relative",
                              top: "-10px",
                              left: "-10px", 
                            }}
                            title={`Override all ${group.pendingCount} pending to pass`}
                          >
                            <img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'%3E%3Cpath fill='white' d='M362.7 19.3L314.3 67.7 444.3 197.7l48.4-48.4c25-25 25-65.5 0-90.5L453.3 19.3c-25-25-65.5-25-90.5 0zm-71 71L58.6 323.5c-10.4 10.4-18 23.3-22.2 37.4L1 481.2C-1.5 489.7 .8 498.8 7 505s15.3 8.5 23.7 6.1l120.3-35.4c14.1-4.2 27-11.8 37.4-22.2L421.7 220.3 291.7 90.3z'/%3E%3C/svg%3E" alt="Override" style={{ width: "10px", height: "10px" }} />
                          </button>
                        </div>
                      )}
                    </div>
                  </td>
                  <td
                    style={{
                      padding: "12px",
                      textAlign: "center",
                    }}
                  >
                    {(() => {
                      // Show all unique validation sources
                      const sources = new Set(group.resources.map(r => r.validation_source).filter(Boolean));
                      return Array.from(sources).map((source, sIdx) => {
                        const src = (source || '').toLowerCase();
                        const label = src === 'aprl' ? 'APRL' : src === 'llm' ? 'LLM' : src === 'heuristic' ? 'Heuristic' : src === 'pendingreview' ? 'PendingReview' : src === 'user' ? 'User' : source;
                        const bg = src === 'aprl' ? '#dbeafe' : src === 'llm' ? '#fef3c7' : src === 'heuristic' ? '#e0e7ff' : src === 'user' ? '#dcfce7' : src === 'pendingreview' ? '#f3e8ff' : '#e5e7eb';
                        const fg = src === 'aprl' ? '#1e40af' : src === 'llm' ? '#92400e' : src === 'heuristic' ? '#3730a3' : src === 'user' ? '#166534' : src === 'pendingreview' ? '#6b21a8' : '#374151';
                        const bd = src === 'aprl' ? '#bfdbfe' : src === 'llm' ? '#fde68a' : src === 'heuristic' ? '#c7d2fe' : src === 'user' ? '#bbf7d0' : src === 'pendingreview' ? '#e9d5ff' : '#d1d5db';
                        
                        return (
                          <span
                            key={sIdx}
                            style={{
                              display: 'inline-block',
                              padding: '2px 6px',
                              borderRadius: '3px',
                              fontSize: '10px',
                              fontWeight: 600,
                              background: bg,
                              color: fg,
                              border: `1px solid ${bd}`,
                              marginBottom: sIdx < sources.size - 1 ? '4px' : '0',
                            }}
                          >
                            {label}
                          </span>
                        );
                      });
                    })()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {groupedRecommendations.length === 0 && (
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
    </div>
  );
};

export default ResiliencySummary;
