import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ReactFlowProvider } from "reactflow";
import GraphCanvas, {
  GraphNode,
  GraphEdge,
  GraphCanvasHandle
} from "../components/GraphCanvasReactflow";
import ResilienceSummary from "../components/ResilienceSummary";
import ZonalResilienceSummary from "../components/ZonalResilienceSummary";
import TabbedView from "../components/TabbedView";
import EdgeDrawer, {
  EdgeData
} from "../components/EdgeDrawer";
import NodeDrawer, { NodeData } from "../components/NodeDrawer";

import WorkloadSidebar from "../components/WorkloadSidebar";
import LegendPanel from "../components/LegendPanel";
import {
  acceptEdge,
  createManualEdge,
  deleteEdge,
  reverseEdgeDirection,
  fetchWorkloadGraph,
  fetchSubscriptions,
  patchNode,
  rejectEdge,
  resetNode,
  createGroup,
  updateGroup,
  deleteGroup,
  addNodeToGroup,
  removeNodeFromGroup,
  type SubscriptionInfo,
} from "../api/workloads";
import {
  buildViewGraph,
  computeResourceGroupOptions,
  computeServiceOptions,
  LEVEL_TO_MAX_IMPORTANCE,
  normalizeGraph,
  type GraphSnapshot,
  type ViewLevel,
} from "../domain/graphView";
import { calculateResilienceScore, getElementWeight, DEFAULT_WEIGHTS, type ResilienceWeights } from "../utils/resilienceScore";
import { getZonalResilience, type ZonalResilienceResponse } from "../api/resilience";

// Subscription-aware view: user selects a subscriptionId

const WorkloadView: React.FC = () => {
  const [subscriptions, setSubscriptions] = useState<SubscriptionInfo[]>([]);
  const [subscriptionId, setSubscriptionId] = useState<string>("");
  const [showSubscriptionPicker, setShowSubscriptionPicker] = useState<boolean>(true);
  const storageKey = useMemo(() => `workload_graph_${subscriptionId}`, [subscriptionId]);
  const [graph, setGraph] = useState<GraphSnapshot | null>(null);
  const [resilience_evaluations, setResilienceEvaluations] = useState<Record<string, any> | null>(null);
  const [resilience_overrides, setResilienceOverrides] = useState<Record<string, any>>({});
  const [resilience_data, setResilienceData] = useState<any | null>(null);
  const [zonal_resilience_data, setZonalResilienceData] = useState<ZonalResilienceResponse | null>(null);
  const [zonal_resilience_loading, setZonalResilienceLoading] = useState(false);
  const [zonal_resilience_error, setZonalResilienceError] = useState<string | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<EdgeData | null>(null);
  const [selectedNode, setSelectedNode] = useState<NodeData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewLevel, setViewLevel] = useState<ViewLevel>("overview");
  const [showLegend, setShowLegend] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState(320);
  const [isResizing, setIsResizing] = useState(false);

  // Default both layers to enabled; no URL sync
  const [aiLayerEnabled, setAiLayerEnabled] = useState(true);
  const [userLayerEnabled, setUserLayerEnabled] = useState(true);
  const [serviceFilter, setServiceFilter] = useState<Set<string>>(new Set());
  const [resourceGroupFilter, setResourceGroupFilter] = useState<Set<string>>(new Set());
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());

  const [groupToolbarSelection, setGroupToolbarSelection] = useState<{
    selectedNodeIds: string[];
    selectedGroupId: string | null;
    selectedGroupLabel?: string;
    selectedGroupMemberIds?: string[];
  }>({ selectedNodeIds: [], selectedGroupId: null });
  const [groupToolbarName, setGroupToolbarName] = useState<string>("");
  const [groupCreateRequest, setGroupCreateRequest] = useState<{ nonce: number; label: string } | null>(null);

  // Track if user has made changes requiring refresh
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Weights for resilience score calculation
  const [resilienceWeights, setResilienceWeights] = useState<ResilienceWeights>(DEFAULT_WEIGHTS);

  const lastSuggestedGroupNameRef = useRef<string>("");
  const lastGroupToolbarSelectionRef = useRef(groupToolbarSelection);
  const graphCanvasRef = useRef<GraphCanvasHandle>(null);

  const readStoredGraph = (): GraphSnapshot | null => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || !Array.isArray(parsed.nodes) || !Array.isArray(parsed.edges)) return null;
      return parsed as GraphSnapshot;
    } catch {
      return null;
    }
  };

  const persistGraph = (next: GraphSnapshot | null) => {
    if (!next) {
      localStorage.removeItem(storageKey);
      return;
    }
    try {
      localStorage.setItem(storageKey, JSON.stringify(next));
    } catch {
      /* ignore storage errors */
    }
  };

  const updateGraph = (updater: (prev: GraphSnapshot | null) => GraphSnapshot | null) => {
    setGraph(prev => {
      const base = prev ?? readStoredGraph();
      const next = updater(base);
      persistGraph(next);
      return next;
    });
  };

  const applyResilienceOverrides = useCallback((evaluations: Record<string, any>, overrides?: Record<string, any>) => {
    // Build lookup by resilience_check_id (deterministic UUIDv5 from resource_id + recommendation_id)
    const overrideLookup = new Map<string, { status: "pass" | "fail" | "pending"; validation_source?: string; resilience_check_id?: string }>();

    Object.entries(overrides || {}).forEach(([resilienceCheckId, override]) => {
      if (!resilienceCheckId) return;

      const validationSource = ((override as any)?.overridden_by || "").toLowerCase() === "user"
        ? "User"
        : (override as any)?.overridden_by;

      overrideLookup.set(resilienceCheckId, {
        status: (override as any)?.status,
        validation_source: validationSource,
        resilience_check_id: resilienceCheckId,
      });
    });

    return Object.fromEntries(
      Object.entries(evaluations || {}).map(([resourceId, evaluation]) => {
        const checksOrFindings = (evaluation as any).findings || (evaluation as any).checks || [];

        const mergedChecks = checksOrFindings.map((check: any) => {
          const resilienceCheckId = check?.resilience_check_id as string | undefined;
          if (!resilienceCheckId) return check;

          const override = overrideLookup.get(resilienceCheckId);
          if (!override) return check;

          return {
            ...check,
            status: override.status,
            validation_source: override.validation_source || check.validation_source,
            resilience_check_id: resilienceCheckId,
          };
        });

        const passed = mergedChecks.filter((c: any) => c.status === "pass").length;
        const failed = mergedChecks.filter((c: any) => c.status === "fail").length;

        return [
          resourceId,
          {
            ...evaluation,
            findings: (evaluation as any).findings ? mergedChecks : undefined,
            checks: (evaluation as any).checks ? mergedChecks : undefined,
            passed_checks: passed,
            failed_checks: failed,
            total_checks: mergedChecks.length,
          },
        ];
      })
    );
  }, []);

  const upsertResilienceOverride = useCallback((override: { resilience_check_id?: string; status: "pass" | "fail" | "pending"; overridden_by?: string; resource_id?: string; recommendation_id?: string }) => {
    const resilienceCheckId = override?.resilience_check_id;
    if (!resilienceCheckId) return;

    setResilienceOverrides(prev => ({
      ...(prev || {}),
      [resilienceCheckId]: {
        ...(prev || {})[resilienceCheckId],
        ...override,
        overridden_by: override.overridden_by ?? "user",
      },
    }));
  }, []);

  const removeResilienceOverride = useCallback((resilienceCheckId: string) => {
    if (!resilienceCheckId) return;

    setResilienceOverrides(prev => {
      const next = { ...(prev || {}) };
      delete next[resilienceCheckId];
      return next;
    });
  }, []);

  const applyOptimisticOverrideRemoval = useCallback((resilienceCheckId: string, resourceId?: string) => {
    if (!resilienceCheckId || !resourceId) return;

    setResilienceEvaluations(prev => {
      if (!prev || !prev[resourceId]) return prev;

      const entry = prev[resourceId];
      const updateList = (checks?: any[]) => {
        if (!checks) return checks;
        return checks.map(check => {
          if (check?.resilience_check_id !== resilienceCheckId) return check;

          const nextCheck = { ...check, status: "fail" as const };
          const validationSource = check?.validation_source;

          if (Array.isArray(validationSource)) {
            const filtered = validationSource.filter((v: string) => String(v).toLowerCase() !== "user");
            nextCheck.validation_source = filtered.length > 0 ? filtered : ["APRL"];
          } else if (typeof validationSource === "string") {
            const lower = validationSource.toLowerCase();
            nextCheck.validation_source = lower === "user" || !validationSource ? "APRL" : validationSource;
          } else {
            nextCheck.validation_source = "APRL";
          }

          return nextCheck;
        });
      };

      const nextFindings = updateList((entry as any).findings);
      const nextChecks = updateList((entry as any).checks);
      const listForCounts = (nextChecks ?? nextFindings ?? []) as any[];

      return {
        ...prev,
        [resourceId]: {
          ...entry,
          findings: (entry as any).findings ? nextFindings : undefined,
          checks: (entry as any).checks ? nextChecks : undefined,
          passed_checks: listForCounts.filter((c: any) => c.status === "pass").length,
          failed_checks: listForCounts.filter((c: any) => c.status === "fail").length,
          total_checks: listForCounts.length,
        },
      };
    });
  }, []);

  const fetchGraph = useCallback(async () => {
    if (!subscriptionId) return;
    try {
      setLoading(true);
      setError(null);

      const raw = await fetchWorkloadGraph(subscriptionId);
      const normalized = normalizeGraph(raw);
      persistGraph(normalized);
      setGraph(normalized);
      
      // Store resilience evaluations and overrides if available
      if (raw.resilience_evaluations) {
        setResilienceOverrides(raw.resilience_overrides || {});
        setResilienceData(raw.resilience_evaluations);
        setResilienceEvaluations(raw.resilience_evaluations.evaluations || {});
      } else {
        setResilienceOverrides(raw.resilience_overrides || {});
        setResilienceData(null);
        setResilienceEvaluations(null);
      }
      
      // Reset filters after loading new graph data so all options are checked
      setServiceFilter(new Set());
      setResourceGroupFilter(new Set());
      setExpandedCategories(new Set());
    } catch (err: any) {
      setError(err.message ?? "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [subscriptionId]);

  const handleOverrideSaved = useCallback((override: { resilience_check_id?: string; status: "pass" | "fail" | "pending"; overridden_by?: string; resource_id?: string; recommendation_id?: string }) => {
    upsertResilienceOverride(override);
  }, [upsertResilienceOverride]);

  const handleOverrideDeleted = useCallback((resilienceCheckId: string, resourceId?: string) => {
    removeResilienceOverride(resilienceCheckId);
    applyOptimisticOverrideRemoval(resilienceCheckId, resourceId);
  }, [removeResilienceOverride, applyOptimisticOverrideRemoval]);

  const fetchZonalResilience = useCallback(async () => {
    if (!subscriptionId) return;
    try {
      setZonalResilienceLoading(true);
      setZonalResilienceError(null);

      const data = await getZonalResilience(subscriptionId);
      setZonalResilienceData(data);
    } catch (err: any) {
      setZonalResilienceError(err.message ?? "Failed to load zonal resilience data");
    } finally {
      setZonalResilienceLoading(false);
    }
  }, [subscriptionId]);

  // Fetch subscriptions on mount
  useEffect(() => {
    fetchSubscriptions()
      .then(subs => {
        setSubscriptions(subs);
        
        // Try to restore last selected subscription if it still exists
        const stored = localStorage.getItem("awg_subscription_id");
        if (stored && subs.some(s => s.id === stored)) {
          setSubscriptionId(stored);
          setShowSubscriptionPicker(false);
          return;
        }

        // No stored/valid subscription: keep picker open and clear stale value
        localStorage.removeItem("awg_subscription_id");
        setShowSubscriptionPicker(true);
      })
      .catch(err => {
        console.error("Failed to fetch subscriptions:", err);
        setShowSubscriptionPicker(true);
      });
  }, []);

  // Fetch weights from backend
  useEffect(() => {
    const loadWeights = async () => {
      try {
        const response = await fetch('/api/resilience/weights');
        if (response.ok) {
          const data = await response.json();
          setResilienceWeights({
            categoryWeights: data.category_weights || DEFAULT_WEIGHTS.categoryWeights,
            impactWeights: data.impact_weights || DEFAULT_WEIGHTS.impactWeights,
          });
        }
      } catch (error) {
        console.error('Failed to load weights from backend, using defaults:', error);
      }
    };
    loadWeights();
  }, []);

  // Fit view when filters change
  useEffect(() => {
    graphCanvasRef.current?.fitView();
  }, [viewLevel, serviceFilter, resourceGroupFilter]);

  // Clear selections when view level changes
  useEffect(() => {
    setSelectedNode(null);
    setSelectedEdge(null);
  }, [viewLevel]);

  const resourceGroupOptions = useMemo(() => {
    if (!graph) return [] as { key: string; label: string }[];
    return computeResourceGroupOptions(graph, viewLevel, aiLayerEnabled, userLayerEnabled);
  }, [graph, viewLevel, aiLayerEnabled, userLayerEnabled]);

  const serviceOptions = useMemo(() => {
    if (!graph) return [];
    return computeServiceOptions(graph, viewLevel, aiLayerEnabled, userLayerEnabled);
  }, [graph, viewLevel, aiLayerEnabled, userLayerEnabled]);

  useEffect(() => {
    if (serviceOptions.length && serviceFilter.size === 0) {
      const allServices = serviceOptions.flatMap(cat => cat.services.map(s => s.key));
      const allCategories = serviceOptions.map(cat => cat.category);
      setServiceFilter(new Set(allServices));
      setExpandedCategories(new Set(allCategories));
    }
  }, [serviceOptions, serviceFilter.size]);

  useEffect(() => {
    if (!resourceGroupOptions.length) {
      if (resourceGroupFilter.size) setResourceGroupFilter(new Set());
      return;
    }

    const optionKeys = new Set(resourceGroupOptions.map(opt => opt.key));

    setResourceGroupFilter(prev => {
      // If nothing selected yet, default to all available groups.
      if (prev.size === 0) {
        return new Set(optionKeys);
      }

      // Keep the user's current selection; avoid shrinking it when the option list changes.
      // This prevents transient option recalculation from hiding nodes unexpectedly.
      return prev;
    });
  }, [resourceGroupOptions, resourceGroupFilter.size]);

  // Build annotation map for element weight lookup
  const annotationMap = useMemo(() => {
    if (!graph?.llm_annotations?.nodes) return new Map();
    const map = new Map<string, any>();
    for (const node of graph.llm_annotations.nodes) {
      map.set(node.node_id.toLowerCase(), node.annotations);
    }
    return map;
  }, [graph]);

  // Merge overrides into evaluations for scoring without mutating cached graph
  const mergedEvaluations = useMemo(() => {
    if (!resilience_evaluations) return null;
    return applyResilienceOverrides(resilience_evaluations, resilience_overrides);
  }, [resilience_evaluations, resilience_overrides, applyResilienceOverrides]);

  // Enrich graph with calculated resilience scores (SINGLE CALCULATION POINT)
  const graphWithScores = useMemo(() => {
    if (!graph || !mergedEvaluations) return graph;

    // Create a case-insensitive lookup map
    const evalLookup = new Map<string, any>();
    Object.entries(mergedEvaluations).forEach(([key, value]) => {
      evalLookup.set(key.toLowerCase(), value);
    });

    const updatedNodes = graph.nodes.map(node => {
      const nodeId = node.id.toLowerCase();
      const evaluation = evalLookup.get(nodeId);
      
      if (!evaluation) {
        // No evaluation available - don't add resilience data
        return node;
      }

      const metadata = (node.metadata as any) || {};
      const resilience = metadata.resilience || {};

      const checks = evaluation.checks || evaluation.findings || [];
      if (checks.length === 0) {
        // No checks - don't add resilience data
        return node;
      }

      const elementWeight = getElementWeight(nodeId, annotationMap);
      const score = calculateResilienceScore(checks, elementWeight, resilienceWeights);

      return {
        ...node,
        element_weight: elementWeight,
        metadata: {
          ...metadata,
          resilience: {
            ...resilience,
            resilience_score: score,
          },
        },
      };
    });

    return {
      ...graph,
      nodes: updatedNodes,
    };
  }, [graph, mergedEvaluations, annotationMap, resilienceWeights]);

  const viewGraph = useMemo(() => {
    if (!graphWithScores) return null;
    return buildViewGraph({
      snapshot: graphWithScores,
      aiLayerEnabled,
      userLayerEnabled,
      serviceFilter,
      resourceGroupFilter,
    });
  }, [graphWithScores, aiLayerEnabled, userLayerEnabled, serviceFilter, resourceGroupFilter]);

  // Persist subscription selection
  useEffect(() => {
    if (subscriptionId) {
      localStorage.setItem("awg_subscription_id", subscriptionId);
    }
  }, [subscriptionId]);

  // Hydrate from local storage for this subscription, then fetch fresh graph
  useEffect(() => {
    if (!subscriptionId) return; // Don't fetch until subscription is selected
    
    const stored = readStoredGraph();
    setGraph(stored);
    fetchGraph();
    fetchZonalResilience();
  }, [subscriptionId, fetchZonalResilience]); // Only re-fetch when subscription changes

  // Accept edge
  const handleAcceptEdge = async (edgeId: string) => {
    try {
      await acceptEdge(subscriptionId, edgeId);

      // Mark as needing refresh
      setNeedsRefresh(true);

      // Optimistic UI update
      updateGraph(prev =>
        prev
          ? {
              ...prev,
              edges: prev.edges.map(e =>
                e.id === edgeId
                  ? { ...e, status: "accepted" }
                  : e
              )
            }
          : prev
      );

      setSelectedEdge(prev =>
        prev && prev.id === edgeId
          ? { ...prev, status: "accepted" }
          : prev
      );
    } catch (err) {
      console.error("Failed to accept edge", err);
    }
  };

  // Reject edge
  const handleRejectEdge = async (edgeId: string) => {
    try {
      await rejectEdge(subscriptionId, edgeId);

      updateGraph(prev =>
        prev
          ? {
              ...prev,
              edges: prev.edges.map(e =>
                e.id === edgeId
                  ? { ...e, status: "rejected" }
                  : e
              )
            }
          : prev
      );

      setSelectedEdge(prev =>
        prev && prev.id === edgeId
          ? { ...prev, status: "rejected" }
          : prev
      );
    } catch (err) {
      console.error("Failed to reject edge", err);
    }
  };

  const handleDeleteEdge = async (edgeId: string) => {
    try {
      await deleteEdge(subscriptionId, edgeId);

      updateGraph(prev =>
        prev
          ? {
              ...prev,
              edges: prev.edges.filter(e => e.id !== edgeId)
            }
          : prev
      );

      setSelectedEdge(prev =>
        prev && prev.id === edgeId ? null : prev
      );
    } catch (err) {
      console.error("Failed to delete edge", err);
    }
  };

  const handleReverseEdgeDirection = async (edgeId: string) => {
    try {
      const result = await reverseEdgeDirection(subscriptionId, edgeId);
      const reversedEdge = result.edge;

      if (reversedEdge) {
        const updated: GraphEdge = {
          id: reversedEdge.id,
          source: reversedEdge.source,
          target: reversedEdge.target,
          relationship: reversedEdge.relationship,
          confidence: reversedEdge.confidence,
          status: reversedEdge.status as any,
          origin: reversedEdge.origin,
          evidence: reversedEdge.evidence,
        };

        setSelectedEdge(updated);

        // Update the edge in place with reversed direction
        updateGraph(prev =>
          prev
            ? {
                ...prev,
                edges: prev.edges.map(e =>
                  e.id === edgeId ? updated : e
                )
              }
            : prev
        );

      }
    } catch (err) {
      console.error("Failed to reverse edge direction", err);
    }
  };

  const buildSelectedNodeData = (node: GraphNode) => {
    const meta = (node.metadata as any) ?? {};
    const userOverride = (meta.user_override as Record<string, unknown> | undefined) ?? {};
    return {
      id: node.id,
      name: node.name,
      type: node.type,
      layer: meta.importance as number | undefined,
      color: meta.color as string | undefined,
      icon: meta.icon as string | undefined,
      override: Object.keys(userOverride).length > 0,
      criticalityScore: meta.criticality_score as number | undefined,
      aiAnnotation: node.metadata?.ai_annotation as any,
      originalName: node.name,
      raw: node,
    };
  };

  const handleNodeSelected = (nodeId: string | null) => {
    if (!nodeId) {
      setSelectedNode(null);
      setSelectedEdge(null);
      return;
    }
    if (!viewGraph) return;

    const node = viewGraph.nodes.find(n => n.id === nodeId);
    if (!node) return;
    setSelectedNode(buildSelectedNodeData(node));
    setSelectedEdge(null);
  };

  const handleCreateManualLink = async (sourceId: string, targetId: string) => {
    if (!sourceId || !targetId || sourceId === targetId) return;

    try {
      const body = await createManualEdge(subscriptionId, {
        source: sourceId,
        target: targetId,
        relationship: "depends_on",
      });
      const created = body.edge;

      if (created) {
        // Mark as needing refresh
        setNeedsRefresh(true);

        const newEdge: GraphEdge = {
          id: created.id,
           source: created.source,
           target: created.target,
          relationship: created.relationship,
          confidence: created.confidence,
          status: created.status as any,
           origin: created.origin,
          evidence: created.evidence,
        };

        updateGraph(prev =>
          prev
            ? {
                ...prev,
                edges: [...prev.edges, newEdge],
              }
            : prev
        );

        // Open the drawer for the newly created edge
        setSelectedEdge(newEdge);
      } else {
        console.error("Failed to create link: no edge returned");
      }
    } catch (err: any) {
      console.error("Failed to create link:", err.message);
    }
  };

  const handleRenameNode = async (nodeId: string) => {
    const node = viewGraph?.nodes.find(n => n.id === nodeId);
    if (!node) return;
    setSelectedNode(buildSelectedNodeData(node));
  };

  const handleSaveNode = async (nodeId: string, payload: { name?: string; layer?: number | null; color?: string | null; icon?: string | null; criticality?: number | null }) => {
    try {
      const nodePatch: Parameters<typeof patchNode>[2] = {
        name: payload.name,
        layer: payload.layer,
        color: payload.color,
        icon: payload.icon,
        criticality_score: payload.criticality,
      };

      await patchNode(subscriptionId, nodeId, nodePatch);

      // Mark as needing refresh
      setNeedsRefresh(true);

      updateGraph(prev => {
        if (!prev) return prev;

        const nextOverride: Record<string, unknown> = {};
        if (payload.name !== undefined) nextOverride.name = payload.name ?? undefined;
        if (payload.layer !== undefined) nextOverride.layer = payload.layer ?? undefined;
        if (payload.color !== undefined) nextOverride.color = payload.color ?? undefined;
        if (payload.icon !== undefined) nextOverride.icon = payload.icon ?? undefined;
        if (payload.criticality !== undefined) nextOverride.criticality_score = payload.criticality ?? undefined;

        // Clean undefined values
        const cleanedOverride = Object.fromEntries(
          Object.entries(nextOverride).filter(([, v]) => v !== undefined)
        );

        const nextNodeOverrides = { ...prev.node_overrides };
        if (Object.keys(cleanedOverride).length > 0) nextNodeOverrides[nodeId] = cleanedOverride;
        else delete nextNodeOverrides[nodeId];

        return {
          ...prev,
          node_overrides: nextNodeOverrides,
          nodes: prev.nodes.map(n => {
            if (n.id !== nodeId) return n;
            const meta = (n.metadata as any) ?? {};
            return {
              ...n,
              name: payload.name ?? n.name,
              metadata: {
                ...meta,
                importance: payload.layer === undefined ? meta.importance : payload.layer ?? meta.importance,
                color: payload.color === undefined ? meta.color : payload.color ?? undefined,
                icon: payload.icon === undefined ? meta.icon : payload.icon ?? undefined,
                criticality_score:
                  payload.criticality === undefined
                    ? meta.criticality_score
                    : payload.criticality === null
                      ? meta.criticality_score
                      : payload.criticality,
                user_override: Object.keys(cleanedOverride).length > 0 ? cleanedOverride : undefined,
              }
            };
          })
        };
      });

      setSelectedNode(prev => {
        if (!prev || prev.id !== nodeId) return prev;

        const rawNode = (prev.raw as any) ?? undefined;
        const rawMeta = rawNode?.metadata ?? {};
        const recomputedOverride: Record<string, unknown> = {};
        if (payload.name !== undefined) recomputedOverride.name = payload.name ?? undefined;
        if (payload.layer !== undefined) recomputedOverride.layer = payload.layer ?? undefined;
        if (payload.color !== undefined) recomputedOverride.color = payload.color ?? undefined;
        if (payload.icon !== undefined) recomputedOverride.icon = payload.icon ?? undefined;
        if (payload.criticality !== undefined) recomputedOverride.criticality_score = payload.criticality ?? undefined;

        // Clean undefined values
        const cleanedRecomputedOverride = Object.fromEntries(
          Object.entries(recomputedOverride).filter(([, v]) => v !== undefined)
        );

        const nextRaw = rawNode
          ? {
              ...rawNode,
              name: payload.name ?? rawNode.name,
              metadata: {
                ...rawMeta,
                user_override: Object.keys(cleanedRecomputedOverride).length > 0 ? cleanedRecomputedOverride : undefined,
              },
            }
          : undefined;

        return {
          ...prev,
          name: payload.name ?? prev.name,
          layer: payload.layer === undefined ? prev.layer : payload.layer ?? undefined,
          color: payload.color === undefined ? prev.color : payload.color ?? undefined,
          icon: payload.icon === undefined ? prev.icon : payload.icon ?? undefined,
          override: Object.keys(cleanedRecomputedOverride).length > 0,
          raw: nextRaw ?? prev.raw,
          criticalityScore: payload.criticality === undefined
            ? prev.criticalityScore
            : (payload.criticality === null ? undefined : payload.criticality),
        };
      });
    } catch (err) {
      console.error("Failed to update node", err);
    }
  };

  const applyGroupToNodes = async (args: { groupId: string; label: string; memberIds: string[] }) => {
    const { groupId, label, memberIds } = args;
    if (!memberIds.length) return;

    // Optimistic UI update - create new group
    updateGraph(prev => {
      if (!prev) return prev;

      const groups = prev.groups ?? [];
      // Remove nodes from any existing groups
      const updatedGroups = groups.map(g => ({
        ...g,
        nodes: g.nodes.filter(id => !memberIds.includes(id)),
      }));
      
      // Add new group
      return {
        ...prev,
        groups: [...updatedGroups, { id: groupId, name: label, nodes: memberIds }],
      };
    });

    // Remove nodes from any existing groups first
    if (graph?.groups) {
      for (const group of graph.groups) {
        for (const nodeId of memberIds) {
          if (group.nodes.includes(nodeId)) {
            await removeNodeFromGroup(subscriptionId, group.id, nodeId);
          }
        }
      }
    }

    // Create the new group
    await createGroup(subscriptionId, { id: groupId, name: label, nodes: memberIds });
  };


  const ungroupNodes = async (args: { groupId: string; memberIds: string[] }) => {
    const { groupId } = args;

    // Optimistic UI update - remove the group
    updateGraph(prev =>
      prev
        ? {
            ...prev,
            groups: (prev.groups ?? []).filter(g => g.id !== groupId),
          }
        : prev
    );

    // Delete the entire group
    await deleteGroup(subscriptionId, groupId);
  };

  const renameGroup = async (args: { groupId: string; label: string; memberIds: string[] }) => {
    const { groupId, label } = args;

    // Optimistic UI update
    updateGraph(prev =>
      prev
        ? {
            ...prev,
            groups: (prev.groups ?? []).map(g => (g.id === groupId ? { ...g, name: label } : g)),
          }
        : prev
    );

    // Update the group name
    await updateGroup(subscriptionId, groupId, { name: label });
  };

  const moveNodeToGroup = async (args: { nodeId: string; groupId: string }) => {
    const { nodeId, groupId } = args;
    if (!graph) return;

    // Optimistic UI update - add node to the group's nodes array
    updateGraph(prev => {
      if (!prev) return prev;

      const groups = prev.groups ?? [];
      const updatedGroups = groups.map(g => {
        if (g.id === groupId && !g.nodes.includes(nodeId)) {
          return { ...g, nodes: [...g.nodes, nodeId] };
        }
        // Remove from other groups
        return { ...g, nodes: g.nodes.filter(id => id !== nodeId) };
      });

      return {
        ...prev,
        groups: updatedGroups,
      };
    });

    // Remove from any existing groups first
    if (graph.groups) {
      for (const group of graph.groups) {
        if (group.nodes.includes(nodeId) && group.id !== groupId) {
          await removeNodeFromGroup(subscriptionId, group.id, nodeId);
        }
      }
    }

    // Add to the new group
    await addNodeToGroup(subscriptionId, groupId, nodeId);
  };

  const handleRemoveNodeFromGroup = async (args: { nodeId: string; groupId: string }) => {
    const { nodeId, groupId } = args;
    if (!graph) return;

    // Optimistic UI update - remove node from the group's nodes array
    updateGraph(prev => {
      if (!prev) return prev;

      const groups = prev.groups ?? [];
      const updatedGroups = groups.map(g => {
        if (g.id === groupId) {
          return { ...g, nodes: g.nodes.filter(id => id !== nodeId) };
        }
        return g;
      });

      return {
        ...prev,
        groups: updatedGroups,
      };
    });

    // Remove from the group
    await removeNodeFromGroup(subscriptionId, groupId, nodeId);
  };

  const handleNodeRemoveFromGroupClick = (args: { nodeId: string; groupId: string }) => {
    // Just call the handler directly - it's synchronous for UI feedback
    handleRemoveNodeFromGroup(args);
  };

  const handleResetNode = async (nodeId: string) => {
    try {
      await resetNode(subscriptionId, nodeId);

      // Remove override from local storage and rebuild graph
      updateGraph(prev => {
        if (!prev) return prev;

        const nextOverrides = { ...prev.node_overrides };
        delete nextOverrides[nodeId];

        return {
          ...prev,
          node_overrides: nextOverrides,
          nodes: prev.nodes.map(n => {
            if (n.id !== nodeId) return n;
            const meta = (n.metadata as any) ?? {};
            const nextMeta = { ...meta };
            delete nextMeta.user_override;
            return {
              ...n,
              metadata: nextMeta,
            };
          }),
        };
      });

      setSelectedNode(null);
    } catch (err) {
      console.error("Failed to reset node", err);
    }
  };

  const handleRefreshAnnotationsAndScores = async () => {
    if (!subscriptionId) return;
    
    try {
      setIsRefreshing(true);
      setError(null);

      // Kick off async LLM refresh
      const response = await fetch(
        `/api/subscriptions/${subscriptionId}/refresh`,
        { method: "POST" }
      );

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "Refresh start failed");
      }

      // Poll status until completed/failed
      const pollStatus = async (): Promise<string> => {
        const s = await fetch(`/api/subscriptions/${subscriptionId}/refresh/status`);
        if (!s.ok) throw new Error("Status check failed");
        const data = await s.json();
        return data.status || "idle";
      };

      let status = await pollStatus();
      const start = Date.now();
      const timeoutMs = 5 * 60 * 1000; // 5 minutes
      while (status === "running" && Date.now() - start < timeoutMs) {
        await new Promise((r) => setTimeout(r, 2000));
        status = await pollStatus();
      }

      if (status === "failed") {
        throw new Error("LLM refresh failed");
      }

      // Refresh the graph from server after completion
      await fetchGraph();

      // Clear the dirty flag
      setNeedsRefresh(false);
    } catch (err: any) {
      console.error("Failed to refresh annotations and scores:", err.message);
      setError(err.message);
    } finally {
      setIsRefreshing(false);
    }
  };

  // Always respect the view level selection
  const maxImportance = LEVEL_TO_MAX_IMPORTANCE[viewLevel];

  const resetFiltersToAll = useCallback(() => {
    // Reset filters - empty sets will trigger useEffect hooks to select all options
    setServiceFilter(new Set());
    setResourceGroupFilter(new Set());
    setExpandedCategories(new Set());
  }, []);

  const handleMouseDown = (e: React.MouseEvent) => {
    setIsResizing(true);
    e.preventDefault();
  };

  const handleMouseMove = useCallback((e: MouseEvent) => {
    if (!isResizing) return;
    const newWidth = Math.max(250, Math.min(600, e.clientX));
    setSidebarWidth(newWidth);
  }, [isResizing]);

  const handleMouseUp = useCallback(() => {
    setIsResizing(false);
  }, []);

  useEffect(() => {
    if (isResizing) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
      return () => {
        document.removeEventListener('mousemove', handleMouseMove);
        document.removeEventListener('mouseup', handleMouseUp);
      };
    }
  }, [isResizing, handleMouseMove, handleMouseUp]);

  const handleSelectSubscription = (subId: string) => {
    setSubscriptionId(subId);
    localStorage.setItem("awg_subscription_id", subId);
    setShowSubscriptionPicker(false);
    resetFiltersToAll();
  };

  if (showSubscriptionPicker) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100vh",
          background: "#0f0f0f",
          color: "#eee",
        }}
      >
        <div
          style={{
            background: "#1a1a1a",
            border: "1px solid #333",
            borderRadius: 8,
            padding: "32px 40px",
            minWidth: 400,
            maxWidth: 600,
          }}
        >
          <h2 style={{ margin: "0 0 16px 0", fontSize: 20, fontWeight: 600 }}>
            Select Subscription
          </h2>
          <p style={{ margin: "0 0 20px 0", fontSize: 14, color: "#9AA0A6" }}>
            Choose a subscription to view its workload graph
          </p>

          {subscriptions.length === 0 ? (
            <div style={{ fontSize: 14, color: "#9AA0A6" }}>
              No subscriptions available. Run the collector first.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {subscriptions.map(sub => (
                <button
                  key={sub.id}
                  onClick={() => handleSelectSubscription(sub.id)}
                  style={{
                    padding: "12px 16px",
                    background: "#2a2a2a",
                    color: "#fff",
                    border: "1px solid #444",
                    borderRadius: 6,
                    cursor: "pointer",
                    fontSize: 14,
                    textAlign: "left",
                    transition: "background 0.2s",
                  }}
                  onMouseEnter={e => {
                    e.currentTarget.style.background = "#333";
                  }}
                  onMouseLeave={e => {
                    e.currentTarget.style.background = "#2a2a2a";
                  }}
                >
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>{sub.name}</div>
                  <div style={{ fontSize: 12, color: "#9AA0A6" }}>{sub.id}</div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div style={{ padding: 16, color: "#ccc" }}>
        Loading workload graph…
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: 16, color: "#f44336" }}>
        {error}
      </div>
    );
  }

  if (!graph) {
    return null;
  }

  const nodesForView = viewGraph?.nodes ?? graph.nodes;
  const edgesForView = viewGraph?.edges ?? graph.edges;

  const suggestGroupName = (selectedIds: string[]): string => {
    if (selectedIds.length === 0) return "";

    const degreeById = new Map<string, number>();
    for (const e of edgesForView) {
      degreeById.set(e.source, (degreeById.get(e.source) ?? 0) + 1);
      degreeById.set(e.target, (degreeById.get(e.target) ?? 0) + 1);
    }

    const candidates = selectedIds
      .map(id => nodesForView.find(n => n.id === id))
      .filter((n): n is (typeof nodesForView)[number] => !!n);

    if (candidates.length === 0) return "";

    const score = (n: (typeof nodesForView)[number]): number => {
      const meta: any = (n as any).metadata ?? {};
      const v = meta.criticality_score;
      return typeof v === "number" ? v : 0;
    };

    const degree = (n: (typeof nodesForView)[number]): number => degreeById.get(n.id) ?? 0;

    const name = (n: (typeof nodesForView)[number]): string => {
      const raw = (n as any).name as string | undefined;
      if (raw && raw.trim()) return raw.trim();
      const last = n.id.split("/").pop();
      return (last && last.trim()) ? last.trim() : n.id;
    };

    const best = [...candidates].sort((a, b) => {
      const s = score(b) - score(a);
      if (s !== 0) return s;

      const d = degree(b) - degree(a);
      if (d !== 0) return d;

      return name(a).localeCompare(name(b));
    })[0];

    return name(best);
  };

  return (
    <div
      style={{
        display: "flex",
        height: "100vh",
        width: "100%",
        overflow: "hidden"
      }}
    >
      {/* Left sidebar */}
      <div
        style={{
          width: sidebarOpen ? sidebarWidth : 0,
          minWidth: sidebarOpen ? sidebarWidth : 0,
          background: "#0f0f0f",
          borderRight: sidebarOpen ? "1px solid #222" : "none",
          transition: isResizing ? "none" : "width 0.3s ease, min-width 0.3s ease",
          overflow: "hidden",
          display: "flex",
          flexDirection: "row",
          flexShrink: 0,
          position: "relative"
        }}
      >
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden"
          }}
        >
          <WorkloadSidebar
            viewLevel={viewLevel}
            onViewLevelChange={setViewLevel}
            aiLayerEnabled={aiLayerEnabled}
            onAiLayerEnabledChange={setAiLayerEnabled}
            userLayerEnabled={userLayerEnabled}
            onUserLayerEnabledChange={setUserLayerEnabled}
            resourceGroupOptions={resourceGroupOptions}
            resourceGroupFilter={resourceGroupFilter}
            onResourceGroupFilterChange={setResourceGroupFilter}
            serviceOptions={serviceOptions}
            serviceFilter={serviceFilter}
            onServiceFilterChange={setServiceFilter}
            expandedCategories={expandedCategories}
            onExpandedCategoriesChange={setExpandedCategories}
            showLegend={showLegend}
            onToggleLegend={() => setShowLegend(prev => !prev)}
          />

          {/* Group toolbar (shows only for multi-select or selected group) */}
          {(() => {
            const hasMultiSelect = groupToolbarSelection.selectedGroupId === null && groupToolbarSelection.selectedNodeIds.length > 1;
            const hasGroupSelected = !!groupToolbarSelection.selectedGroupId;
            if (!hasMultiSelect && !hasGroupSelected) return null;

            const isSaveDisabled = groupToolbarName.trim().length === 0;

            return (
              <div
                style={{
                  padding: "10px 12px 12px 12px",
                  background: "#0f0f0f",
                  borderTop: "1px solid #222",
                  borderBottom: "1px solid #222",
                  display: "flex",
                  flexDirection: "column",
                  gap: 8,
                  color: "#eee",
                  flexShrink: 0,
                }}
              >
                <div style={{ fontSize: 12, color: "#9AA0A6" }}>Group</div>

                <input
                  value={groupToolbarName}
                  onChange={e => setGroupToolbarName(e.target.value)}
                  placeholder={hasMultiSelect ? "Enter group name" : "Group name"}
                  style={{
                    padding: "8px 10px",
                    background: "#181818",
                    color: "#fff",
                    border: "1px solid #333",
                    borderRadius: 4,
                    fontSize: 13,
                  }}
                />

                <div style={{ display: "flex", gap: 8 }}>
                  <button
                    disabled={isSaveDisabled}
                    onClick={async () => {
                      const label = groupToolbarName.trim();
                      if (!label) return;

                      if (hasMultiSelect) {
                        setGroupCreateRequest({ nonce: Date.now(), label });
                        setGroupToolbarName("");
                        lastSuggestedGroupNameRef.current = "";
                      } else if (hasGroupSelected) {
                        const gid = groupToolbarSelection.selectedGroupId!;
                        const members = groupToolbarSelection.selectedGroupMemberIds ?? [];
                        if (members.length) await renameGroup({ groupId: gid, label, memberIds: members });
                      }
                    }}
                    style={{
                      flex: 1,
                      padding: "8px 10px",
                      background: isSaveDisabled ? "#2a2a2a" : "#1f2937",
                      color: isSaveDisabled ? "#777" : "#fff",
                      border: "1px solid #333",
                      borderRadius: 4,
                      cursor: isSaveDisabled ? "not-allowed" : "pointer",
                      fontSize: 13,
                      fontWeight: 600,
                    }}
                  >
                    Save
                  </button>

                  {hasGroupSelected && (
                    <button
                      onClick={async () => {
                        const gid = groupToolbarSelection.selectedGroupId!;
                        const members = groupToolbarSelection.selectedGroupMemberIds ?? [];
                        if (members.length) await ungroupNodes({ groupId: gid, memberIds: members });
                        setGroupToolbarName("");
                      }}
                      style={{
                        flex: 1,
                        padding: "8px 10px",
                        background: "#1f2937",
                        color: "#fff",
                        border: "1px solid #333",
                        borderRadius: 4,
                        cursor: "pointer",
                        fontSize: 13,
                        fontWeight: 600,
                      }}
                    >
                      Ungroup
                    </button>
                  )}
                </div>
              </div>
            );
          })()}
        </div>

        {/* Resize handle */}
        {sidebarOpen && (
          <div
            onMouseDown={handleMouseDown}
            style={{
              width: 5,
              cursor: "ew-resize",
              background: isResizing ? "#85a2c6ff" : "transparent",
              transition: "background 0.2s",
              position: "relative",
              flexShrink: 0
            }}
            onMouseEnter={(e) => {
              if (!isResizing) {
                e.currentTarget.style.background = "#333";
              }
            }}
            onMouseLeave={(e) => {
              if (!isResizing) {
                e.currentTarget.style.background = "transparent";
              }
            }}
          />
        )}
      </div>

      {/* Main content area */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
        {/* Top bar with toggle */}
        <div
          style={{
            padding: "10px 14px",
            background: "#0f0f0f",
            borderBottom: "1px solid #222",
            display: "flex",
            gap: 12,
            alignItems: "center",
            color: "#eee"
          }}
        >
          <button
            onClick={() => setSidebarOpen(prev => !prev)}
            style={{
              padding: "6px 12px",
              background: "#1f2937",
              color: "#fff",
              border: "1px solid #333",
              borderRadius: 4,
              cursor: "pointer",
              fontSize: 13
            }}
            title="Toggle sidebar"
          >
            {sidebarOpen ? "◀ Hide" : "▶ Show"} Menu
          </button>
          <h2 style={{ margin: 0, fontSize: 16, color: "#eee", flex: 1 }}>Azure Resilience IQ</h2>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <label htmlFor="subscriptionId" style={{ fontSize: 12, color: "#9AA0A6" }}>Subscription</label>
            <select
              id="subscriptionId"
              value={subscriptionId}
              onChange={e => {
                const newId = e.target.value;
                setSubscriptionId(newId);
                localStorage.setItem("awg_subscription_id", newId);
              }}
              style={{
                padding: "6px 8px",
                background: "#181818",
                color: "#fff",
                border: "1px solid #333",
                borderRadius: 4,
                fontSize: 12,
                minWidth: 260,
                cursor: "pointer",
              }}
            >
              {subscriptions.map(sub => (
                <option key={sub.id} value={sub.id}>
                  {sub.name}
                </option>
              ))}
            </select>
            <button
              onClick={() => {
                resetFiltersToAll();
                setShowSubscriptionPicker(true);
              }}
              style={{
                padding: "6px 12px",
                background: "#1f2937",
                color: "#fff",
                border: "1px solid #333",
                borderRadius: 4,
                cursor: "pointer",
                fontSize: 12
              }}
              title="Change subscription"
            >
              Change
            </button>
            <button
              onClick={() => {
                fetchGraph();
                fetchZonalResilience();
              }}
              disabled={!subscriptionId}
              style={{
                padding: "6px 12px",
                background: subscriptionId ? "#1f2937" : "#2a2a2a",
                color: subscriptionId ? "#fff" : "#777",
                border: "1px solid #333",
                borderRadius: 4,
                cursor: subscriptionId ? "pointer" : "not-allowed",
                fontSize: 12
              }}
              title="Reload graph and zonal resilience data from server"
            >
              Reload
            </button>
            
            {/* Refresh annotations and scores button with badge */}
            {needsRefresh && (
              <div style={{ position: "relative" }}>
                <button
                  onClick={() => handleRefreshAnnotationsAndScores()}
                  disabled={isRefreshing}
                  style={{
                    padding: "6px 12px",
                    background: isRefreshing ? "#444" : "#2ea043",
                    color: "#fff",
                    border: "1px solid #4a7c4e",
                    borderRadius: 4,
                    cursor: isRefreshing ? "wait" : "pointer",
                    fontSize: 12,
                    fontWeight: 600,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                  title="Re-run LLM annotations and resilience scoring based on your changes"
                >
                  {isRefreshing ? "Refreshing..." : "✓ Refresh Annotations & Scores"}
                </button>
                <span
                  style={{
                    position: "absolute",
                    top: "-8px",
                    right: "-8px",
                    background: "#ff6b6b",
                    color: "#fff",
                    borderRadius: "50%",
                    width: 16,
                    height: 16,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 10,
                    fontWeight: 700,
                  }}
                  title="Updates pending"
                >
                  !
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Tabbed View: Graph and Resilience */}
        <div style={{ flex: 1, height: "100%", display: "flex", flexDirection: "column" }}>
          <TabbedView
            tabs={[
              {
                label: "Graph",
                icon: "📊",
                content: (
                  <div style={{ width: "100%", height: "100%" }}>
                    <ReactFlowProvider>
                      <GraphCanvas
                        ref={graphCanvasRef}
                        nodes={nodesForView}
                        edges={edgesForView}
                        selectedEdgeId={selectedEdge?.id ?? null}
                        userLayerEnabled={userLayerEnabled}
                        maxImportance={maxImportance}
                        onNodeSelected={handleNodeSelected}
                        onEdgeCreate={handleCreateManualLink}
                        onNodeRename={handleRenameNode}
                        onGroupCreate={applyGroupToNodes}
                        groupCreateRequest={groupCreateRequest}
                        onSelectionStateChange={state => {
                          const prevSelection = lastGroupToolbarSelectionRef.current;
                          lastGroupToolbarSelectionRef.current = state;
                          setGroupToolbarSelection(state);

                          // Initialize toolbar name when mode changes or selecting a different group.
                          if (state.selectedGroupId) {
                            lastSuggestedGroupNameRef.current = "";
                            setGroupToolbarName(prev => {
                              if (prev.trim().length === 0 || prev === (prevSelection.selectedGroupLabel ?? "")) {
                                return state.selectedGroupLabel ?? "";
                              }
                              return prev;
                            });
                          } else if (state.selectedNodeIds.length > 1) {
                            const suggested = suggestGroupName(state.selectedNodeIds);
                            setGroupToolbarName(prev => {
                              const shouldReplace =
                                prev.trim().length === 0 || prev === lastSuggestedGroupNameRef.current;
                              if (!shouldReplace) return prev;

                              lastSuggestedGroupNameRef.current = suggested;
                              return suggested;
                            });
                          } else {
                            lastSuggestedGroupNameRef.current = "";
                            setGroupToolbarName("");
                          }
                        }}
                        onMoveNodeToGroup={moveNodeToGroup}
                        onNodeRemoveFromGroup={handleNodeRemoveFromGroupClick}
                        onEdgeSelected={(e) => {
                          if (!e) {
                            setSelectedEdge(null);
                            return;
                          }

                          setSelectedNode(null);
                          setSelectedEdge({
                            id: e.id,
                            source: e.source,
                            target: e.target,
                            relationship: e.relationship,
                            confidence: e.confidence,
                            status: e.status as any,
                            evidence: e.evidence,
                            origin: e.origin,
                            raw: e,
                          });
                        }}
                      />
                    </ReactFlowProvider>
                  </div>
                ),
              },
              {
                label: "Overview",
                icon: "🛡️",
                content: resilience_evaluations ? (
                  <ResilienceSummary
                    evaluations={resilience_evaluations || {}}
                    workloadScore={resilience_data?.workload_score}
                    subscriptionId={subscriptionId}
                    graphData={graph}
                    overrides={resilience_overrides}
                    viewLevel={viewLevel}
                    resourceGroupFilter={resourceGroupFilter}
                    serviceFilter={serviceFilter}
                    onOverrideSaved={handleOverrideSaved}
                    onOverrideDeleted={handleOverrideDeleted}
                  />
                ) : (
                  <div style={{ padding: "32px", textAlign: "center", color: "#6b7280" }}>
                    No resilience data available
                  </div>
                ),
              },
              {
                label: "Zonal Resilience",
                icon: "🌍",
                content: zonal_resilience_loading ? (
                  <div style={{ padding: "32px", textAlign: "center", color: "#6b7280" }}>
                    Loading zonal resilience data...
                  </div>
                ) : zonal_resilience_error ? (
                  <div style={{ padding: "32px", color: "#dc2626" }}>
                    <strong>Error:</strong> {zonal_resilience_error}
                  </div>
                ) : zonal_resilience_data ? (
                  <ZonalResilienceSummary 
                    data={zonal_resilience_data} 
                    graphData={graph}
                    resourceGroupFilter={resourceGroupFilter}
                    serviceFilter={serviceFilter}
                  />
                ) : (
                  <div style={{ padding: "32px", textAlign: "center", color: "#6b7280" }}>
                    No zonal resilience data available
                  </div>
                ),
              },
            ]}
            defaultTab={0}
          />
        </div>
      </div>

      {/* Right drawer */}
      {selectedEdge && (
        <EdgeDrawer
          edge={selectedEdge}
          onAccept={handleAcceptEdge}
          onReject={handleRejectEdge}
          onDelete={handleDeleteEdge}
          onReverseDirection={handleReverseEdgeDirection}
          onClose={() => setSelectedEdge(null)}
        />
      )}

      {selectedNode && (
        <NodeDrawer
          node={selectedNode}
          aiLayerEnabled={aiLayerEnabled}
          userLayerEnabled={userLayerEnabled}
          onClose={() => setSelectedNode(null)}
          onSave={handleSaveNode}
          onReset={() => handleResetNode(selectedNode.id)}
        />
      )}
      <LegendPanel open={showLegend} onClose={() => setShowLegend(false)} />
    </div>
  );
};

export default WorkloadView;
