import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ReactFlowProvider } from "reactflow";
import GraphCanvas, {
  GraphNode,
  GraphEdge,
  GraphCanvasHandle
} from "../components/GraphCanvasReactflow";
import ResiliencySummary from "../components/ResiliencySummary";
import ZonalResiliencySummary from "../components/ZonalResiliencySummary";
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
  clearBridgeEdges,
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
  listWorkloads,
  getWorkload,
  createWorkload,
  updateWorkload,
  deleteWorkload,
  type SubscriptionInfo,
  type WorkloadRecord,
  type WorkloadSummary,
  type WorkloadViewState,
} from "../api/workloads";
import {
  buildViewGraph,
  computeResourceGroupOptions,
  computeServiceOptions,
  computeValidationSourceOptions,
  LEVEL_TO_MAX_IMPORTANCE,
  normalizeGraph,
  type GraphSnapshot,
  type ViewLevel,
} from "../domain/graphView";
import { calculateResiliencyScore, getElementWeight, DEFAULT_WEIGHTS, type ResiliencyWeights } from "../utils/resilienceScore";
import { getZonalResiliency, type ZonalResiliencyResponse } from "../api/resilience";
import { mergeGraphSnapshots, mergeResiliencyEvaluations, mergeZonalResiliencyData } from "../utils/multiSubscriptionMerge";
import { ArrowCollapseAll16Regular, ArrowExpandAll16Regular } from "@fluentui/react-icons";

// Subscription-aware view: user selects one or more subscriptions

const WorkloadView: React.FC = () => {
  const [subscriptions, setSubscriptions] = useState<SubscriptionInfo[]>([]);
  const [selectedSubscriptions, setSelectedSubscriptions] = useState<Set<string>>(new Set());
  const selectedSubscriptionIds = useMemo(
    () => Array.from(selectedSubscriptions).sort(),
    [selectedSubscriptions]
  );
  const selectedSubscriptionOptions = useMemo(
    () =>
      subscriptions
        .filter(sub => selectedSubscriptions.has(sub.id))
        .map(sub => ({ id: sub.id, name: sub.name })),
    [subscriptions, selectedSubscriptions]
  );
  const selectionKey = useMemo(
    () => selectedSubscriptionIds.join("|"),
    [selectedSubscriptionIds]
  );
  const singleSubscriptionId = useMemo(
    () => (selectedSubscriptionIds.length === 1 ? selectedSubscriptionIds[0] : null),
    [selectedSubscriptionIds]
  );
  const storageKey = useMemo(
    () => (selectionKey ? `workload_graph_${selectionKey}` : "workload_graph_none"),
    [selectionKey]
  );
  const [graph, setGraph] = useState<GraphSnapshot | null>(null);
  const [resilience_evaluations, setResiliencyEvaluations] = useState<Record<string, any> | null>(null);
  const [resilience_overrides, setResiliencyOverrides] = useState<Record<string, any>>({});
  const [resilience_data, setResiliencyData] = useState<any | null>(null);
  const [zonal_resilience_data, setZonalResiliencyData] = useState<ZonalResiliencyResponse | null>(null);
  const [zonal_resilience_loading, setZonalResiliencyLoading] = useState(false);
  const [zonal_resilience_error, setZonalResiliencyError] = useState<string | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<EdgeData | null>(null);
  const [selectedNode, setSelectedNode] = useState<NodeData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewLevel, setViewLevel] = useState<ViewLevel>("overview");
  const [showLegend, setShowLegend] = useState(false);
  const hiddenResourcesCount = useMemo(() => {
    if (!graph?.node_overrides) return 0;
    return Object.values(graph.node_overrides).filter((override: any) => override?.hidden === true).length;
  }, [graph]);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState(320);
  const [isResizing, setIsResizing] = useState(false);
  const [activeSubscriptionId, setActiveSubscriptionId] = useState<string | null>(null);

  const [workloads, setWorkloads] = useState<WorkloadSummary[]>([]);
  const [activeWorkloadId, setActiveWorkloadId] = useState<string | null>(null);
  const [activeWorkloadState, setActiveWorkloadState] = useState<WorkloadViewState | null>(null);
  const [workloadName, setWorkloadName] = useState("");
  const [workloadError, setWorkloadError] = useState<string | null>(null);

  // Default both layers to enabled; no URL sync
  const [aiLayerEnabled, setAiLayerEnabled] = useState(true);
  const [userLayerEnabled, setUserLayerEnabled] = useState(true);
  const [serviceFilter, setServiceFilter] = useState<Set<string>>(new Set());
  const [resourceGroupFilter, setResourceGroupFilter] = useState<Set<string>>(new Set());
  const [validationSourceFilter, setValidationSourceFilter] = useState<Set<string>>(new Set());
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());

  const serviceFilterUserTouchedRef = React.useRef(false);
  const resourceGroupFilterUserTouchedRef = React.useRef(false);
  const validationSourceFilterUserTouchedRef = React.useRef(false);

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
  const [pendingRefreshSubscriptions, setPendingRefreshSubscriptions] = useState<Set<string>>(new Set());

  // Weights for resilience score calculation
  const [resilienceWeights, setResiliencyWeights] = useState<ResiliencyWeights>(DEFAULT_WEIGHTS);

  const lastSuggestedGroupNameRef = useRef<string>("");
  const lastGroupToolbarSelectionRef = useRef(groupToolbarSelection);
  const graphCanvasRef = useRef<GraphCanvasHandle>(null);
  const skipFilterResetRef = useRef(false);
  const pendingWorkloadApplyRef = useRef(false);
  const [pendingGraphView, setPendingGraphView] = useState<WorkloadViewState["graph_view"] | null>(null);
  const skipNextFitViewRef = useRef(false);

  const pendingRefreshCount = useMemo(
    () => pendingRefreshSubscriptions.size,
    [pendingRefreshSubscriptions]
  );

  const markSubscriptionDirty = useCallback((subscriptionId: string | null) => {
    if (!subscriptionId) return;
    setPendingRefreshSubscriptions(prev => {
      const next = new Set(prev);
      next.add(subscriptionId);
      return next;
    });
    setNeedsRefresh(true);
  }, []);

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

  const buildWorkloadViewState = useCallback((): WorkloadViewState => ({
    selected_subscriptions: selectedSubscriptionIds,
    view_level: viewLevel,
    ai_layer_enabled: aiLayerEnabled,
    user_layer_enabled: userLayerEnabled,
    resource_group_filter: Array.from(resourceGroupFilter),
    service_filter: Array.from(serviceFilter),
    expanded_categories: Array.from(expandedCategories),
    show_legend: showLegend,
    graph_view: graphCanvasRef.current?.getViewState() ?? undefined,
  }), [selectedSubscriptionIds, viewLevel, aiLayerEnabled, userLayerEnabled, resourceGroupFilter, serviceFilter, expandedCategories, showLegend]);

  const normalizeWorkloadViewState = useCallback((state: WorkloadViewState): WorkloadViewState => {
    const sort = (values: string[]) => [...values].map(String).sort();
    const normalizePositions = (positions?: Record<string, { x: number; y: number }>) => {
      if (!positions) return {} as Record<string, { x: number; y: number }>;
      return Object.fromEntries(
        Object.entries(positions)
          .map(([id, pos]) => [id, { x: pos.x, y: pos.y }])
          .sort(([a], [b]) => String(a).localeCompare(String(b)))
      );
    };
    return {
      selected_subscriptions: sort(state.selected_subscriptions || []),
      view_level: state.view_level || "overview",
      ai_layer_enabled: state.ai_layer_enabled ?? true,
      user_layer_enabled: state.user_layer_enabled ?? true,
      resource_group_filter: sort(state.resource_group_filter || []),
      service_filter: sort(state.service_filter || []),
      expanded_categories: sort(state.expanded_categories || []),
      show_legend: !!state.show_legend,
      graph_view: state.graph_view
        ? {
            viewport: state.graph_view.viewport
              ? { x: state.graph_view.viewport.x, y: state.graph_view.viewport.y, zoom: state.graph_view.viewport.zoom }
              : undefined,
            node_positions: normalizePositions(state.graph_view.node_positions),
          }
        : undefined,
    };
  }, []);

  const isWorkloadDirty = useMemo(() => {
    if (!activeWorkloadId || !activeWorkloadState) return false;
    const current = normalizeWorkloadViewState(buildWorkloadViewState());
    const saved = normalizeWorkloadViewState(activeWorkloadState);
    return JSON.stringify(current) !== JSON.stringify(saved);
  }, [activeWorkloadId, activeWorkloadState, buildWorkloadViewState, normalizeWorkloadViewState]);

  const isNewWorkloadDirty = useMemo(() => {
    if (activeWorkloadId) return false;
    const current = normalizeWorkloadViewState(buildWorkloadViewState());
    const defaultState = normalizeWorkloadViewState({
      selected_subscriptions: [],
      view_level: "overview",
      ai_layer_enabled: true,
      user_layer_enabled: true,
      resource_group_filter: [],
      service_filter: [],
      expanded_categories: [],
      show_legend: false,
    });
    return JSON.stringify(current) !== JSON.stringify(defaultState);
  }, [activeWorkloadId, buildWorkloadViewState, normalizeWorkloadViewState]);

  const applyWorkloadViewState = useCallback((state: WorkloadViewState) => {
    skipFilterResetRef.current = true;
    pendingWorkloadApplyRef.current = true;
    setPendingGraphView(state.graph_view ?? null);
    if (state.graph_view) {
      skipNextFitViewRef.current = true;
    }
    setSelectedSubscriptions(new Set(state.selected_subscriptions || []));
    setViewLevel((state.view_level as ViewLevel) || "overview");
    setAiLayerEnabled(state.ai_layer_enabled ?? true);
    setUserLayerEnabled(state.user_layer_enabled ?? true);
    setResourceGroupFilter(new Set(state.resource_group_filter || []));
    setServiceFilter(new Set(state.service_filter || []));
    setExpandedCategories(new Set(state.expanded_categories || []));
    setShowLegend(!!state.show_legend);
  }, []);

  const loadWorkloads = useCallback(async () => {
    try {
      const list = await listWorkloads();
      setWorkloads(list);
    } catch (err: any) {
      setWorkloadError(err.message ?? "Failed to load workloads");
    }
  }, []);

  const applyResiliencyOverrides = useCallback((evaluations: Record<string, any>, overrides?: Record<string, any>) => {
    // Build lookup by resilience_check_id (deterministic UUIDv5 from resource_id + recommendation_id)
    // NOTE: We only override status; we intentionally keep the original validation_source so
    // PendingReview/User sources remain the same after an override.
    const overrideLookup = new Map<string, { status: "pass" | "fail" | "pending"; resilience_check_id?: string }>();

    Object.entries(overrides || {}).forEach(([resilienceCheckId, override]) => {
      if (!resilienceCheckId) return;

      overrideLookup.set(resilienceCheckId, {
        status: (override as any)?.status,
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
            // Preserve the original validation_source to keep PendingReview/User intact
            validation_source: check.validation_source,
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

  const upsertResiliencyOverride = useCallback((override: { resilience_check_id?: string; status: "pass" | "fail" | "pending"; overridden_by?: string; resource_id?: string; recommendation_id?: string }) => {
    const resilienceCheckId = override?.resilience_check_id;
    if (!resilienceCheckId) return;

    setResiliencyOverrides(prev => ({
      ...(prev || {}),
      [resilienceCheckId]: {
        ...(prev || {})[resilienceCheckId],
        ...override,
        overridden_by: override.overridden_by ?? "user",
      },
    }));
  }, []);

  const removeResiliencyOverride = useCallback((resilienceCheckId: string) => {
    if (!resilienceCheckId) return;

    setResiliencyOverrides(prev => {
      const next = { ...(prev || {}) };
      delete next[resilienceCheckId];
      return next;
    });
  }, []);

  const applyOptimisticOverrideRemoval = useCallback((resilienceCheckId: string, resourceId?: string) => {
    if (!resilienceCheckId || !resourceId) return;

    setResiliencyEvaluations(prev => {
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
    if (selectedSubscriptionIds.length === 0) return;
    try {
      setLoading(true);
      setError(null);

      const results = await Promise.all(
        selectedSubscriptionIds.map(async subscriptionId => {
          const raw = await fetchWorkloadGraph(subscriptionId);
          const normalized = normalizeGraph(raw);
          return { subscriptionId, raw, graph: normalized };
        })
      );

      const mergedGraph = mergeGraphSnapshots(
        results.map(item => ({ subscriptionId: item.subscriptionId, graph: item.graph }))
      );

      persistGraph(mergedGraph);
      setGraph(mergedGraph);

      const mergedEvaluations = mergeResiliencyEvaluations(
        results.map(item => ({
          subscriptionId: item.subscriptionId,
          evaluations: item.raw.resilience_evaluations?.evaluations || {},
        }))
      );

      const mergedOverrides = results.reduce<Record<string, any>>((acc, item) => {
        return { ...acc, ...(item.raw.resilience_overrides || {}) };
      }, {});

      if (Object.keys(mergedEvaluations).length > 0) {
        setResiliencyEvaluations(mergedEvaluations);
        setResiliencyData({ evaluations: mergedEvaluations });
      } else {
        setResiliencyEvaluations(null);
        setResiliencyData(null);
      }

      setResiliencyOverrides(mergedOverrides);

      if (skipFilterResetRef.current) {
        skipFilterResetRef.current = false;
      } else {
        // Reset filters after loading new graph data so all options are checked
        setServiceFilter(new Set());
        setResourceGroupFilter(new Set());
        setExpandedCategories(new Set());
      }
    } catch (err: any) {
      setError(err.message ?? "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [selectedSubscriptionIds, storageKey]);

  const handleOverrideSaved = useCallback((override: { resilience_check_id?: string; status: "pass" | "fail" | "pending"; overridden_by?: string; resource_id?: string; recommendation_id?: string }) => {
    upsertResiliencyOverride(override);
    if (override?.resource_id) {
      const parts = override.resource_id.split("/").filter(p => p);
      const subId = parts[0]?.toLowerCase() === "subscriptions" ? parts[1] : null;
      if (subId) markSubscriptionDirty(subId);
    }
  }, [upsertResiliencyOverride, markSubscriptionDirty]);

  const handleOverrideDeleted = useCallback((resilienceCheckId: string, resourceId?: string) => {
    removeResiliencyOverride(resilienceCheckId);
    applyOptimisticOverrideRemoval(resilienceCheckId, resourceId);
    if (resourceId) {
      const parts = resourceId.split("/").filter(p => p);
      const subId = parts[0]?.toLowerCase() === "subscriptions" ? parts[1] : null;
      if (subId) markSubscriptionDirty(subId);
    }
  }, [removeResiliencyOverride, applyOptimisticOverrideRemoval, markSubscriptionDirty]);

  const fetchZonalResiliency = useCallback(async () => {
    if (selectedSubscriptionIds.length === 0) return;
    try {
      setZonalResiliencyLoading(true);
      setZonalResiliencyError(null);

      const dataList = await Promise.all(
        selectedSubscriptionIds.map(async subscriptionId => ({
          subscriptionId,
          data: await getZonalResiliency(subscriptionId),
        }))
      );

      setZonalResiliencyData(mergeZonalResiliencyData(dataList));
    } catch (err: any) {
      setZonalResiliencyError(err.message ?? "Failed to load zonal resilience data");
    } finally {
      setZonalResiliencyLoading(false);
    }
  }, [selectedSubscriptionIds]);

  // Fetch subscriptions on mount
  useEffect(() => {
    fetchSubscriptions()
      .then(subs => {
        setSubscriptions(subs);

        const storedMulti = localStorage.getItem("awg_subscription_ids");
        let restored: string[] = [];

        if (storedMulti) {
          try {
            const parsed = JSON.parse(storedMulti);
            if (Array.isArray(parsed)) restored = parsed.map(String);
          } catch {
            restored = [];
          }
        }

        if (restored.length === 0) {
          const storedSingle = localStorage.getItem("awg_subscription_id");
          if (storedSingle) restored = [storedSingle];
        }

        const valid = restored.filter(id => subs.some(s => s.id === id));
        setSelectedSubscriptions(new Set(valid));

        if (valid.length === 0) {
          localStorage.removeItem("awg_subscription_id");
          localStorage.removeItem("awg_subscription_ids");
        }
      })
      .catch(err => {
        console.error("Failed to fetch subscriptions:", err);
      });
  }, []);

  useEffect(() => {
    loadWorkloads();
  }, [loadWorkloads]);

  // Fetch weights from backend
  useEffect(() => {
    const loadWeights = async () => {
      try {
        const response = await fetch('/api/resilience/weights');
        if (response.ok) {
          const data = await response.json();
          setResiliencyWeights({
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
    if (skipNextFitViewRef.current) {
      skipNextFitViewRef.current = false;
      return;
    }
    graphCanvasRef.current?.fitView();
  }, [viewLevel, serviceFilter, resourceGroupFilter]);

  useEffect(() => {
    if (!graph) return;
  }, [graph]);

  // Clear selections when view level changes
  useEffect(() => {
    setSelectedNode(null);
    setSelectedEdge(null);
  }, [viewLevel]);

  const resourceGroupOptions = useMemo(() => {
    if (!graph) return [] as { key: string; label: string }[];
    return computeResourceGroupOptions(graph, viewLevel, aiLayerEnabled, userLayerEnabled);
  }, [graph, viewLevel, aiLayerEnabled, userLayerEnabled]);

  const handleResourceGroupFilterChange = useCallback((next: Set<string>) => {
    resourceGroupFilterUserTouchedRef.current = true;
    setResourceGroupFilter(next);
  }, []);

  const serviceOptions = useMemo(() => {
    if (!graph) return [];
    return computeServiceOptions(graph, viewLevel, aiLayerEnabled, userLayerEnabled);
  }, [graph, viewLevel, aiLayerEnabled, userLayerEnabled]);

  const handleServiceFilterChange = useCallback((next: Set<string>) => {
    serviceFilterUserTouchedRef.current = true;
    setServiceFilter(next);
  }, []);

  const validationSourceOptions = useMemo(() => {
    return computeValidationSourceOptions(resilience_evaluations);
  }, [resilience_evaluations]);

  const handleValidationSourceFilterChange = useCallback((next: Set<string>) => {
    validationSourceFilterUserTouchedRef.current = true;
    setValidationSourceFilter(next);
  }, []);

  useEffect(() => {
    if (!serviceOptions.length) {
      if (expandedCategories.size) setExpandedCategories(new Set());
      return;
    }

    // Auto-populate serviceFilter if it's empty (after subscription change or reset)
    if (!serviceFilterUserTouchedRef.current && serviceFilter.size === 0) {
      const allServices = serviceOptions.flatMap(cat => cat.services.map(s => s.key));
      setServiceFilter(new Set(allServices));
      return;
    }

    const mixedCategories = new Set<string>();
    serviceOptions.forEach(category => {
      const allServicesInCategory = category.services.map(s => s.key);
      const selectedCount = allServicesInCategory.filter(key => serviceFilter.has(key)).length;
      if (selectedCount > 0 && selectedCount < allServicesInCategory.length) {
        mixedCategories.add(category.category);
      }
    });

    setExpandedCategories(mixedCategories);
  }, [serviceOptions, serviceFilter]);

  useEffect(() => {
    if (!resourceGroupOptions.length) {
      if (pendingWorkloadApplyRef.current) return;
      if (resourceGroupFilter.size) setResourceGroupFilter(new Set());
      return;
    }

    if (pendingWorkloadApplyRef.current) {
      pendingWorkloadApplyRef.current = false;
      return;
    }

    // Auto-populate resourceGroupFilter if it's empty (after subscription change or reset)
    if (!resourceGroupFilterUserTouchedRef.current && resourceGroupFilter.size === 0) {
      const allGroups = resourceGroupOptions.map(rg => rg.key);
      setResourceGroupFilter(new Set(allGroups));
      return;
    }

    // Keep the user's current selection; avoid shrinking it when the option list changes.
    // This prevents transient option recalculation from hiding nodes unexpectedly.
  }, [resourceGroupOptions, resourceGroupFilter.size]);

  useEffect(() => {
    if (!validationSourceOptions.length) {
      if (validationSourceFilter.size) setValidationSourceFilter(new Set());
      return;
    }

    // Auto-populate only if user hasn't intentionally changed it (e.g., uncheck all)
    if (!validationSourceFilterUserTouchedRef.current && validationSourceFilter.size === 0) {
      const allSources = validationSourceOptions.map(s => s.key);
      setValidationSourceFilter(new Set(allSources));
      return;
    }
  }, [validationSourceOptions, validationSourceFilter.size]);

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
    return applyResiliencyOverrides(resilience_evaluations, resilience_overrides);
  }, [resilience_evaluations, resilience_overrides, applyResiliencyOverrides]);

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
      const score = calculateResiliencyScore(checks, elementWeight, resilienceWeights);

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

  const resolveSubscriptionIdForEdge = useCallback((edgeId: string): string | null => {
    // Edges are always stored in the source node's subscription
    const edge = graph?.edges.find(e => e.id === edgeId) ?? viewGraph?.edges.find(e => e.id === edgeId);
    if (!edge) {
      setError("Edge not found.");
      return null;
    }
    
    // Use the source node's subscription (where the edge is stored)
    const sourceNode = graph?.nodes.find(n => n.id === edge.source) ?? viewGraph?.nodes.find(n => n.id === edge.source);
    const sourceSub = (sourceNode as any)?.subscription_id ?? (sourceNode?.metadata as any)?.subscription_id;
    if (sourceSub) return String(sourceSub);
    
    // Fallback to single subscription only if source node lookup fails
    if (singleSubscriptionId) return singleSubscriptionId;
    
    setError("Unable to determine subscription for edge source node. Please select a single subscription.");
    return null;
  }, [graph, viewGraph, singleSubscriptionId]);

  const resolveSubscriptionIdForNode = useCallback((nodeId: string): string | null => {
    const node = graph?.nodes.find(n => n.id === nodeId) ?? viewGraph?.nodes.find(n => n.id === nodeId);
    const nodeSub = (node as any)?.subscription_id ?? (node?.metadata as any)?.subscription_id;
    if (nodeSub) return String(nodeSub);
    if (singleSubscriptionId) return singleSubscriptionId;
    setError("Select a single subscription to modify resources.");
    return null;
  }, [graph, viewGraph, singleSubscriptionId]);

  const resolveSubscriptionIdForNodeIds = useCallback((nodeIds: string[]): string | null => {
    const subs = new Set<string>();
    nodeIds.forEach(nodeId => {
      const node = graph?.nodes.find(n => n.id === nodeId) ?? viewGraph?.nodes.find(n => n.id === nodeId);
      const nodeSub = (node as any)?.subscription_id ?? (node?.metadata as any)?.subscription_id;
      if (nodeSub) subs.add(String(nodeSub));
    });

    if (subs.size === 1) return Array.from(subs)[0];
    if (subs.size === 0 && singleSubscriptionId) return singleSubscriptionId;

    setError("Cross-subscription edits are not supported.");
    return null;
  }, [graph, viewGraph, singleSubscriptionId]);

  // Persist subscription selection
  useEffect(() => {
    if (selectedSubscriptionIds.length > 0) {
      localStorage.setItem("awg_subscription_ids", JSON.stringify(selectedSubscriptionIds));
      if (selectedSubscriptionIds.length === 1) {
        localStorage.setItem("awg_subscription_id", selectedSubscriptionIds[0]);
      } else {
        localStorage.removeItem("awg_subscription_id");
      }
    } else {
      localStorage.removeItem("awg_subscription_ids");
      localStorage.removeItem("awg_subscription_id");
    }
  }, [selectedSubscriptionIds]);

  // Hydrate from local storage for this subscription, then fetch fresh graph
  useEffect(() => {
    if (selectedSubscriptionIds.length === 0) {
      setGraph(null);
      setResiliencyEvaluations(null);
      setResiliencyOverrides({});
      setResiliencyData(null);
      setZonalResiliencyData(null);
      setPendingRefreshSubscriptions(new Set());
      setNeedsRefresh(false);
      return;
    }

    const stored = readStoredGraph();
    setGraph(stored);
    fetchGraph();
    fetchZonalResiliency();
  }, [selectionKey, selectedSubscriptionIds.length, fetchGraph, fetchZonalResiliency]);

  // Accept edge
  const handleAcceptEdge = async (edgeId: string) => {
    try {
      const edgeSubscriptionId = resolveSubscriptionIdForEdge(edgeId);
      if (!edgeSubscriptionId) return;

      await acceptEdge(edgeSubscriptionId, edgeId);

      // Mark as needing refresh
      markSubscriptionDirty(edgeSubscriptionId);

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
      const edgeSubscriptionId = resolveSubscriptionIdForEdge(edgeId);
      if (!edgeSubscriptionId) return;

      await rejectEdge(edgeSubscriptionId, edgeId);

      markSubscriptionDirty(edgeSubscriptionId);

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
      const edgeSubscriptionId = resolveSubscriptionIdForEdge(edgeId);
      if (!edgeSubscriptionId) return;

      await deleteEdge(edgeSubscriptionId, edgeId);

      markSubscriptionDirty(edgeSubscriptionId);

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
      const edge = graph?.edges.find(e => e.id === edgeId) ?? viewGraph?.edges.find(e => e.id === edgeId);
      if (!edge) return;

      const oldSourceSub = resolveSubscriptionIdForNode(edge.source);
      const newSourceSub = resolveSubscriptionIdForNode(edge.target);

      if (!oldSourceSub || !newSourceSub) return;

      if (oldSourceSub === newSourceSub) {
        const result = await reverseEdgeDirection(oldSourceSub, edgeId);
        const reversedEdge = result.edge;

        markSubscriptionDirty(oldSourceSub);

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

        return;
      }

      await deleteEdge(oldSourceSub, edgeId);
      markSubscriptionDirty(oldSourceSub);

      const created = await createManualEdge(newSourceSub, {
        source: edge.target,
        target: edge.source,
        relationship: edge.relationship,
      });

      const newEdge = created.edge;
      if (!newEdge) return;

      markSubscriptionDirty(newSourceSub);

      const updated: GraphEdge = {
        id: newEdge.id,
        source: newEdge.source,
        target: newEdge.target,
        relationship: newEdge.relationship,
        confidence: newEdge.confidence,
        status: newEdge.status as any,
        origin: newEdge.origin,
        evidence: newEdge.evidence,
      };

      setSelectedEdge(updated);

      updateGraph(prev =>
        prev
          ? {
              ...prev,
              edges: [...prev.edges.filter(e => e.id !== edgeId), updated],
            }
          : prev
      );
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
      setActiveSubscriptionId(null);
      return;
    }
    if (!viewGraph) return;

    const node = viewGraph.nodes.find(n => n.id === nodeId);
    if (!node) return;
    setSelectedNode(buildSelectedNodeData(node));
    setSelectedEdge(null);
    setActiveSubscriptionId(resolveSubscriptionIdForNode(nodeId));
  };

  const handleCreateManualLink = async (sourceId: string, targetId: string) => {
    if (!sourceId || !targetId || sourceId === targetId) return;

    try {
      const edgeSubscriptionId = resolveSubscriptionIdForNode(sourceId);
      if (!edgeSubscriptionId) return;

      const body = await createManualEdge(edgeSubscriptionId, {
        source: sourceId,
        target: targetId,
        relationship: "depends_on",
      });
      const created = body.edge;

      if (created) {
        // Mark as needing refresh
        markSubscriptionDirty(edgeSubscriptionId);

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

  const handleHideNode = async (nodeId: string) => {
    const nodeSubscriptionId = resolveSubscriptionIdForNode(nodeId);
    if (!nodeSubscriptionId) return;

    try {
      const response = await patchNode(nodeSubscriptionId, nodeId, { hidden: true });

      // Optimistic local updates: drop node, associated edges, group membership, and resilience data
      updateGraph(prev => {
        if (!prev) return prev;
        const remainingNodes = (prev.nodes || []).filter(n => n.id !== nodeId);
        const remainingEdges = (prev.edges || []).filter(e => e.source !== nodeId && e.target !== nodeId);
        
        // Add the bridge edges returned by the API
        const newEdges = remainingEdges;
        if (response?.bridge_edges && Array.isArray(response.bridge_edges)) {
          for (const bridgeEdge of response.bridge_edges) {
            newEdges.push({
              id: bridgeEdge.id,
              source: bridgeEdge.source,
              target: bridgeEdge.target,
              relationship: bridgeEdge.relationship,
              confidence: bridgeEdge.confidence,
              status: bridgeEdge.status,
              origin: bridgeEdge.origin,
              created_by: bridgeEdge.created_by,
            });
          }
        }
        
        const remainingGroups = (prev.groups || []).map(g => ({
          ...g,
          nodes: Array.isArray(g.nodes) ? g.nodes.filter(id => id !== nodeId) : [],
        }));
        // Update node_overrides to track that this node is hidden
        const updatedOverrides = { ...prev.node_overrides };
        updatedOverrides[nodeId] = { ...updatedOverrides[nodeId], hidden: true };
        return { ...prev, nodes: remainingNodes, edges: newEdges, groups: remainingGroups, node_overrides: updatedOverrides };
      });

      setResiliencyEvaluations(prev => {
        if (!prev) return prev;
        const next = { ...prev } as Record<string, any>;
        delete next[nodeId];
        return next;
      });

      setResiliencyData((prev: any) => {
        if (!prev?.evaluations) return prev;
        const nextEvals = { ...prev.evaluations } as Record<string, any>;
        delete nextEvals[nodeId];
        return { ...prev, evaluations: nextEvals };
      });

      setSelectedNode(null);
      setSelectedEdge(null);
      markSubscriptionDirty(nodeSubscriptionId);
    } catch (err) {
      console.error("Failed to hide node", err);
    }
  };

  const handleRestoreAllHiddenResources = async () => {
    if (!graph?.node_overrides || selectedSubscriptionIds.length === 0) return;

    const hiddenNodeIds = Object.entries(graph.node_overrides)
      .filter(([_, override]: [string, any]) => override?.hidden === true)
      .map(([nodeId]) => nodeId);

    if (hiddenNodeIds.length === 0) return;

    try {
      // Helper to extract subscription ID from resource ID
      const extractSubscriptionId = (resourceId: string): string | null => {
        const match = resourceId.match(/\/subscriptions\/([^/]+)/);
        return match ? match[1] : null;
      };

      // Collect all patch operations
      const patchOperations: Array<{nodeId: string; subscriptionId: string}> = [];
      const dirtySubscriptions = new Set<string>();

      hiddenNodeIds.forEach(nodeId => {
        // Try to get subscription ID in order of preference:
        // 1. From node metadata in graph
        const node = graph?.nodes?.find(n => n.id === nodeId);
        let subscriptionId = (node as any)?.subscription_id ?? (node?.metadata as any)?.subscription_id;
        
        // 2. Extract from resource ID itself
        if (!subscriptionId) {
          subscriptionId = extractSubscriptionId(nodeId);
        }
        
        // 3. Use single subscription if available
        if (!subscriptionId && singleSubscriptionId) {
          subscriptionId = singleSubscriptionId;
        }
        
        // 4. Try all selected subscriptions (for edge cases)
        if (!subscriptionId && selectedSubscriptionIds.length > 0) {
          subscriptionId = selectedSubscriptionIds[0];
        }
        
        if (subscriptionId) {
          patchOperations.push({ nodeId, subscriptionId });
          dirtySubscriptions.add(subscriptionId);
        }
      });

      // Clear bridge edges for all affected subscriptions before restoring
      for (const subId of dirtySubscriptions) {
        try {
          await clearBridgeEdges(subId);
        } catch (err) {
          console.warn(`Failed to clear bridge edges for ${subId}:`, err);
        }
      }

      // Execute all patch operations in parallel
      const patchPromises = patchOperations.map(({ nodeId, subscriptionId }) =>
        patchNode(subscriptionId, nodeId, { hidden: false })
      );

      await Promise.all(patchPromises);

      // Mark all affected subscriptions dirty
      dirtySubscriptions.forEach(subId => markSubscriptionDirty(subId));

      // Trigger full refresh to reload graph with restored nodes
      await fetchGraph();
    } catch (err) {
      console.error("Failed to restore hidden resources", err);
      // Silently continue - the refresh might still work
    }
  };;

  const handleRenameNode = async (nodeId: string) => {
    const node = viewGraph?.nodes.find(n => n.id === nodeId);
    if (!node) return;
    setSelectedNode(buildSelectedNodeData(node));
  };

  const handleSaveNode = async (nodeId: string, payload: { name?: string; layer?: number | null; color?: string | null; icon?: string | null; criticality?: number | null }) => {
    try {
      const nodeSubscriptionId = resolveSubscriptionIdForNode(nodeId);
      if (!nodeSubscriptionId) return;

      const nodePatch: Parameters<typeof patchNode>[2] = {
        name: payload.name,
        layer: payload.layer,
        color: payload.color,
        icon: payload.icon,
        criticality_score: payload.criticality,
      };

      await patchNode(nodeSubscriptionId, nodeId, nodePatch);

      // Mark as needing refresh
      markSubscriptionDirty(nodeSubscriptionId);

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

    const groupSubscriptionId = resolveSubscriptionIdForNodeIds(memberIds);
    if (!groupSubscriptionId) return;

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
            await removeNodeFromGroup(groupSubscriptionId, group.id, nodeId);
          }
        }
      }
    }

    // Create the new group
    await createGroup(groupSubscriptionId, { id: groupId, name: label, nodes: memberIds });
    markSubscriptionDirty(groupSubscriptionId);
  };


  const ungroupNodes = async (args: { groupId: string; memberIds: string[] }) => {
    const { groupId } = args;

    const groupSubscriptionId = resolveSubscriptionIdForNodeIds(args.memberIds);
    if (!groupSubscriptionId) return;

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
    await deleteGroup(groupSubscriptionId, groupId);
    markSubscriptionDirty(groupSubscriptionId);
  };

  const renameGroup = async (args: { groupId: string; label: string; memberIds: string[] }) => {
    const { groupId, label } = args;

    const groupSubscriptionId = resolveSubscriptionIdForNodeIds(args.memberIds);
    if (!groupSubscriptionId) return;

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
    await updateGroup(groupSubscriptionId, groupId, { name: label });
    markSubscriptionDirty(groupSubscriptionId);
  };

  const moveNodeToGroup = async (args: { nodeId: string; groupId: string }) => {
    const { nodeId, groupId } = args;
    if (!graph) return;

    const nodeSubscriptionId = resolveSubscriptionIdForNode(nodeId);
    if (!nodeSubscriptionId) return;

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
          await removeNodeFromGroup(nodeSubscriptionId, group.id, nodeId);
        }
      }
    }

    // Add to the new group
    await addNodeToGroup(nodeSubscriptionId, groupId, nodeId);
    markSubscriptionDirty(nodeSubscriptionId);
  };

  const handleRemoveNodeFromGroup = async (args: { nodeId: string; groupId: string }) => {
    const { nodeId, groupId } = args;
    if (!graph) return;

    const nodeSubscriptionId = resolveSubscriptionIdForNode(nodeId);
    if (!nodeSubscriptionId) return;

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
    await removeNodeFromGroup(nodeSubscriptionId, groupId, nodeId);
    markSubscriptionDirty(nodeSubscriptionId);
  };

  const handleNodeRemoveFromGroupClick = (args: { nodeId: string; groupId: string }) => {
    // Just call the handler directly - it's synchronous for UI feedback
    handleRemoveNodeFromGroup(args);
  };

  const handleResetNode = async (nodeId: string) => {
    try {
      const nodeSubscriptionId = resolveSubscriptionIdForNode(nodeId);
      if (!nodeSubscriptionId) return;

      await resetNode(nodeSubscriptionId, nodeId);

      markSubscriptionDirty(nodeSubscriptionId);

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
    const subscriptionsToRefresh = Array.from(pendingRefreshSubscriptions);
    if (subscriptionsToRefresh.length === 0) {
      setError("No subscription changes to refresh.");
      return;
    }
    
    try {
      setIsRefreshing(true);
      setError(null);

      const refreshOne = async (subscriptionId: string): Promise<void> => {
        const response = await fetch(
          `/api/subscriptions/${subscriptionId}/refresh`,
          { method: "POST" }
        );

        if (!response.ok) {
          const errorData = await response.json();
          throw new Error(errorData.detail || "Refresh start failed");
        }

        const statusUrl = response.headers.get("Location")
          ?? `/api/subscriptions/${subscriptionId}/refresh/status`;

        const pollStatus = async (): Promise<string> => {
          const s = await fetch(statusUrl);
          if (s.status === 304) return "running";
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
      };

      const results = await Promise.allSettled(
        subscriptionsToRefresh.map(subId => refreshOne(subId))
      );

      const failed = results
        .map((result, index) => ({ result, subscriptionId: subscriptionsToRefresh[index] }))
        .filter(item => item.result.status === "rejected")
        .map(item => item.subscriptionId);

      setPendingRefreshSubscriptions(prev => {
        const next = new Set(prev);
        subscriptionsToRefresh.forEach(subId => {
          if (!failed.includes(subId)) next.delete(subId);
        });
        return next;
      });

      if (failed.length > 0) {
        setError(`Refresh failed for ${failed.length} subscription(s).`);
      }

      // Refresh the graph from server after completion
      await fetchGraph();

      // Clear the dirty flag if nothing pending
      setNeedsRefresh(failed.length > 0);
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
    setValidationSourceFilter(new Set());
    setExpandedCategories(new Set());
    serviceFilterUserTouchedRef.current = false;
    resourceGroupFilterUserTouchedRef.current = false;
    validationSourceFilterUserTouchedRef.current = false;
  }, []);

  const handleSelectedSubscriptionsChange = useCallback((next: Set<string>) => {
    setSelectedSubscriptions(next);
    resetFiltersToAll();
  }, [resetFiltersToAll]);

  const handleWorkloadSelect = useCallback(async (workloadId: string | null) => {
    setWorkloadError(null);
    if (!workloadId) {
      setActiveWorkloadId(null);
      setWorkloadName("");
      return;
    }
    try {
      const workload = await getWorkload(workloadId);
      setActiveWorkloadId(workload.workload_id);
      setWorkloadName(workload.name);
      setActiveWorkloadState(workload.view_state);
      applyWorkloadViewState(workload.view_state);
    } catch (err: any) {
      setWorkloadError(err.message ?? "Failed to load workload");
    }
  }, [applyWorkloadViewState]);

  const handleWorkloadCreate = useCallback(async () => {
    const name = workloadName.trim();
    if (!name) {
      setWorkloadError("Workload name is required.");
      return;
    }
    try {
      setWorkloadError(null);
      const view_state = buildWorkloadViewState();
      const created = await createWorkload({ name, view_state });
      setActiveWorkloadId(created.workload_id);
      setActiveWorkloadState(created.view_state);
      await loadWorkloads();
    } catch (err: any) {
      setWorkloadError(err.message ?? "Failed to save workload");
    }
  }, [workloadName, buildWorkloadViewState, loadWorkloads]);

  const handleWorkloadSave = useCallback(async () => {
    if (!activeWorkloadId) return;
    try {
      setWorkloadError(null);
      const view_state = buildWorkloadViewState();
      const updated = await updateWorkload(activeWorkloadId, { view_state });
      setActiveWorkloadState(updated.view_state);
      await loadWorkloads();
    } catch (err: any) {
      setWorkloadError(err.message ?? "Failed to update workload");
    }
  }, [activeWorkloadId, buildWorkloadViewState, loadWorkloads]);

  const handleWorkloadRename = useCallback(async () => {
    if (!activeWorkloadId) return;
    const name = workloadName.trim();
    if (!name) {
      setWorkloadError("Workload name is required.");
      return;
    }
    try {
      setWorkloadError(null);
      const updated = await updateWorkload(activeWorkloadId, { name });
      setWorkloadName(updated.name);
      await loadWorkloads();
    } catch (err: any) {
      setWorkloadError(err.message ?? "Failed to rename workload");
    }
  }, [activeWorkloadId, workloadName, loadWorkloads]);

  const handleWorkloadDelete = useCallback(async () => {
    if (!activeWorkloadId) return;
    try {
      setWorkloadError(null);
      await deleteWorkload(activeWorkloadId);
      setActiveWorkloadId(null);
      setActiveWorkloadState(null);
      setWorkloadName("");
      await loadWorkloads();
    } catch (err: any) {
      setWorkloadError(err.message ?? "Failed to delete workload");
    }
  }, [activeWorkloadId, loadWorkloads]);

  useEffect(() => {
    if (activeSubscriptionId && selectedSubscriptionIds.includes(activeSubscriptionId)) return;
    if (singleSubscriptionId) {
      setActiveSubscriptionId(singleSubscriptionId);
      return;
    }
    setActiveSubscriptionId(null);
  }, [activeSubscriptionId, selectedSubscriptionIds, singleSubscriptionId]);

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

  const nodesForView = viewGraph?.nodes ?? graph?.nodes ?? [];
  const edgesForView = viewGraph?.edges ?? graph?.edges ?? [];
  const hasSelection = selectedSubscriptionIds.length > 0;

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
        overflow: "hidden",
        fontFamily: "Segoe UI, Tahoma, Geneva, Verdana, sans-serif",
      }}
    >
      {/* Left sidebar */}
      <div
        style={{
          width: sidebarOpen ? sidebarWidth : 0,
          minWidth: sidebarOpen ? sidebarWidth : 0,
          background: "#f5f5f5",
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
            subscriptions={subscriptions.map(sub => ({ id: sub.id, name: sub.name }))}
            selectedSubscriptions={selectedSubscriptions}
            onSelectedSubscriptionsChange={handleSelectedSubscriptionsChange}
            workloads={workloads}
            activeWorkloadId={activeWorkloadId}
            workloadName={workloadName}
            onWorkloadNameChange={setWorkloadName}
            onWorkloadSelect={handleWorkloadSelect}
            onWorkloadCreate={handleWorkloadCreate}
            onWorkloadSave={handleWorkloadSave}
            onWorkloadRename={handleWorkloadRename}
            onWorkloadDelete={handleWorkloadDelete}
            workloadError={workloadError}
            workloadDirty={isWorkloadDirty}
            workloadNewDirty={isNewWorkloadDirty}
            viewLevel={viewLevel}
            onViewLevelChange={setViewLevel}
            resourceGroupOptions={resourceGroupOptions}
            resourceGroupFilter={resourceGroupFilter}
            onResourceGroupFilterChange={handleResourceGroupFilterChange}
            serviceOptions={serviceOptions}
            serviceFilter={serviceFilter}
            onServiceFilterChange={handleServiceFilterChange}
            validationSourceOptions={validationSourceOptions}
            validationSourceFilter={validationSourceFilter}
            onValidationSourceFilterChange={handleValidationSourceFilterChange}
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
                  background: "#fff",
                  borderTop: "1px solid #e0e0e0",
                  borderBottom: "1px solid #e0e0e0",
                  display: "flex",
                  flexDirection: "column",
                  gap: 8,
                  color: "#323130",
                  flexShrink: 0,
                }}
              >
                <div style={{ fontSize: 12, color: "#605e5c", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px" }}>Group</div>

                <input
                  value={groupToolbarName}
                  onChange={e => setGroupToolbarName(e.target.value)}
                  placeholder={hasMultiSelect ? "Enter group name" : "Group name"}
                  style={{
                    padding: "5px 8px",
                    background: "#fff",
                    color: "#323130",
                    border: "1px solid #8a8886",
                    borderRadius: 2,
                    fontSize: 13,
                    outline: "none",
                  }}
                  onFocus={e => e.target.style.borderColor = "#0078d4"}
                  onBlur={e => e.target.style.borderColor = "#8a8886"}
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
                      padding: "6px 12px",
                      background: isSaveDisabled ? "#f3f2f1" : "#0078d4",
                      color: isSaveDisabled ? "#a19f9d" : "#fff",
                      border: isSaveDisabled ? "1px solid #c8c6c4" : "1px solid #0078d4",
                      borderRadius: 2,
                      cursor: isSaveDisabled ? "not-allowed" : "pointer",
                      fontSize: 13,
                      fontWeight: 400,
                      transition: "all 0.1s ease-in-out",
                    }}
                    onMouseEnter={e => {
                      if (!isSaveDisabled) e.currentTarget.style.background = "#106ebe";
                    }}
                    onMouseLeave={e => {
                      if (!isSaveDisabled) e.currentTarget.style.background = "#0078d4";
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
                        padding: "6px 12px",
                        background: "transparent",
                        color: "#0078d4",
                        border: "1px solid #8a8886",
                        borderRadius: 2,
                        cursor: "pointer",
                        fontSize: 13,
                        fontWeight: 400,
                        transition: "all 0.1s ease-in-out",
                      }}
                      onMouseEnter={e => {
                        e.currentTarget.style.background = "rgba(0, 120, 212, 0.05)";
                        e.currentTarget.style.borderColor = "#0078d4";
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.background = "transparent";
                        e.currentTarget.style.borderColor = "#8a8886";
                      }}
                    >
                      Ungroup
                    </button>
                  )}
                </div>

                {hasMultiSelect && (
                  <button
                    onClick={async () => {
                      const nodeIds = groupToolbarSelection.selectedNodeIds;
                      for (const nodeId of nodeIds) {
                        await handleHideNode(nodeId);
                      }
                    }}
                    style={{
                      width: "100%",
                      padding: "6px 12px",
                      background: "transparent",
                      color: "#a4262c",
                      border: "1px solid #8a8886",
                      borderRadius: 2,
                      cursor: "pointer",
                      fontSize: 13,
                      fontWeight: 400,
                      transition: "all 0.1s ease-in-out",
                    }}
                    onMouseEnter={e => {
                      e.currentTarget.style.background = "rgba(164, 38, 44, 0.05)";
                      e.currentTarget.style.borderColor = "#a4262c";
                    }}
                    onMouseLeave={e => {
                      e.currentTarget.style.background = "transparent";
                      e.currentTarget.style.borderColor = "#8a8886";
                    }}
                    title={`Hide ${groupToolbarSelection.selectedNodeIds.length} selected resource${groupToolbarSelection.selectedNodeIds.length > 1 ? "s" : ""}`}
                  >
                    ✕ Hide All ({groupToolbarSelection.selectedNodeIds.length})
                  </button>
                )}
              </div>
            );
          })()}

          {/* Restore hidden resources */}
          {hiddenResourcesCount > 0 && (
            <div
              style={{
                padding: "2px 12px",
                flexShrink: 0,
              }}
            >
              <button
                onClick={handleRestoreAllHiddenResources}
                style={{
                  width: "100%",
                  padding: "6px 12px",
                  background: "transparent",
                  color: "#0078d4",
                  border: "1px solid #8a8886",
                  borderRadius: 2,
                  cursor: "pointer",
                  fontSize: 13,
                  fontWeight: 400,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 6,
                  transition: "all 0.1s ease-in-out",
                }}
                onMouseEnter={e => {
                  e.currentTarget.style.background = "rgba(0, 120, 212, 0.05)";
                  e.currentTarget.style.borderColor = "#0078d4";
                }}
                onMouseLeave={e => {
                  e.currentTarget.style.background = "transparent";
                  e.currentTarget.style.borderColor = "#8a8886";
                }}
              >
                <span>↺</span>
                <span>Restore {hiddenResourcesCount} hidden resource{hiddenResourcesCount > 1 ? "s" : ""}</span>
              </button>
            </div>
          )}
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
            background: "#f5f5f5",
            borderBottom: "1px solid #e0e0e0",
            display: "flex",
            gap: 12,
            alignItems: "center",
            color: "#323130"
          }}
        >
          <button
            onClick={() => setSidebarOpen(prev => !prev)}
            style={{
              border: "0px",
              cursor: "pointer",
              fontSize: 13,
              transition: "all 0.1s ease-in-out",
              background: "transparent",
            }}
            title="Toggle sidebar"
            onMouseEnter={e => {
              e.currentTarget.style.background = "#ebf4fc";
              e.currentTarget.style.borderColor = "#0078d4";
            }}
            onMouseLeave={e => {
              e.currentTarget.style.background = "transparent";
              e.currentTarget.style.borderColor = "#8a8886";
            }}
          >
            {sidebarOpen ? <ArrowCollapseAll16Regular style={{ fontSize: 16, rotate: "-90deg" }} /> : <ArrowExpandAll16Regular style={{ fontSize: 16, rotate: "-90deg" }} />}
          </button>
          <h2 style={{ margin: 0, fontSize: 16, color: "#323130", flex: 1 }}>Azure Resiliency IQ</h2>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button
              onClick={() => {
                fetchGraph();
                fetchZonalResiliency();
              }}
              disabled={selectedSubscriptionIds.length === 0}
              style={{
                padding: "6px 12px",
                background: selectedSubscriptionIds.length > 0 ? "#fff" : "#f3f2f1",
                color: selectedSubscriptionIds.length > 0 ? "#0078d4" : "#a0a09f",
                border: selectedSubscriptionIds.length > 0 ? "1px solid #8a8886" : "1px solid #d0d0d0",
                borderRadius: 4,
                cursor: selectedSubscriptionIds.length > 0 ? "pointer" : "not-allowed",
                fontSize: 12,
                transition: "all 0.1s ease-in-out",
              }}
              title="Reload graph and zonal resilience data from server"
              onMouseEnter={e => {
                if (selectedSubscriptionIds.length > 0) {
                  e.currentTarget.style.background = "#f3f2f1";
                  e.currentTarget.style.borderColor = "#0078d4";
                }
              }}
              onMouseLeave={e => {
                if (selectedSubscriptionIds.length > 0) {
                  e.currentTarget.style.background = "#fff";
                  e.currentTarget.style.borderColor = "#8a8886";
                }
              }}
            >
              Reload
            </button>
            
            {/* Refresh annotations and scores button with badge */}
            {needsRefresh && (
              <div style={{ position: "relative" }}>
                <button
                  onClick={() => handleRefreshAnnotationsAndScores()}
                  disabled={isRefreshing || pendingRefreshCount === 0}
                  style={{
                    padding: "6px 12px",
                    background: isRefreshing || pendingRefreshCount === 0 ? "#444" : "#2ea043",
                    color: "#fff",
                    border: "1px solid #4a7c4e",
                    borderRadius: 4,
                    cursor: isRefreshing || pendingRefreshCount === 0 ? "not-allowed" : "pointer",
                    fontSize: 12,
                    fontWeight: 600,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                  title={
                    pendingRefreshCount > 0
                      ? `Re-run LLM annotations for ${pendingRefreshCount} subscription(s)`
                      : "No subscription changes to refresh"
                  }
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
                    title={`${pendingRefreshCount} subscription(s) pending refresh`}
                >
                  {pendingRefreshCount}
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Tabbed View: Graph and Resiliency */}
        <div style={{ flex: 1, height: "100%", display: "flex", flexDirection: "column" }}>
          <TabbedView
            tabs={[
              {
                label: "Graph",
                icon: "📊",
                content: !hasSelection ? (
                  <div style={{ padding: "32px", textAlign: "center", color: "#6b7280" }}>
                    Select one or more subscriptions in the sidebar to load the graph.
                  </div>
                ) : (
                  <div style={{ width: "100%", height: "100%" }}>
                    <ReactFlowProvider>
                      <GraphCanvas
                        ref={graphCanvasRef}
                        nodes={nodesForView}
                        edges={edgesForView}
                        graphViewState={pendingGraphView}
                        onGraphViewApplied={() => setPendingGraphView(null)}
                        selectedEdgeId={selectedEdge?.id ?? null}
                        userLayerEnabled={userLayerEnabled}
                        aiLayerEnabled={aiLayerEnabled}
                        onAiLayerEnabledChange={setAiLayerEnabled}
                        onUserLayerEnabledChange={setUserLayerEnabled}
                        maxImportance={maxImportance}
                        onNodeSelected={handleNodeSelected}
                        onEdgeCreate={handleCreateManualLink}
                        onNodeRename={handleRenameNode}
                        onNodeHide={handleHideNode}
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
                            setActiveSubscriptionId(null);
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
                          setActiveSubscriptionId(resolveSubscriptionIdForEdge(e.id));
                        }}
                      />
                    </ReactFlowProvider>
                  </div>
                ),
              },
              {
                label: "Overview",
                icon: "🛡️",
                content: !hasSelection ? (
                  <div style={{ padding: "32px", textAlign: "center", color: "#6b7280" }}>
                    Select one or more subscriptions to view resilience findings.
                  </div>
                ) : resilience_evaluations ? (
                  <ResiliencySummary
                    evaluations={resilience_evaluations || {}}
                    workloadScore={resilience_data?.workload_score}
                    subscriptionId={singleSubscriptionId ?? undefined}
                    subscriptionOptions={selectedSubscriptionOptions}
                    graphData={graph ?? undefined}
                    overrides={resilience_overrides}
                    viewLevel={viewLevel}
                    resourceGroupFilter={resourceGroupFilter}
                    serviceFilter={serviceFilter}
                    validationSourceFilter={validationSourceFilter}
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
                label: "Zonal Resiliency",
                icon: "🌍",
                content: !hasSelection ? (
                  <div style={{ padding: "32px", textAlign: "center", color: "#6b7280" }}>
                    Select one or more subscriptions to view zonal resilience.
                  </div>
                ) : zonal_resilience_loading ? (
                  <div style={{ padding: "32px", textAlign: "center", color: "#6b7280" }}>
                    Loading zonal resilience data...
                  </div>
                ) : zonal_resilience_error ? (
                  <div style={{ padding: "32px", color: "#dc2626" }}>
                    <strong>Error:</strong> {zonal_resilience_error}
                  </div>
                ) : zonal_resilience_data ? (
                  <ZonalResiliencySummary 
                    data={zonal_resilience_data} 
                    graphData={graph ?? undefined}
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
