import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ReactFlowProvider } from "reactflow";
import GraphCanvas, {
  GraphNode,
  GraphEdge
} from "../components/GraphCanvasReactflow";
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
  fetchWorkloadGraph,
  patchNode,
  patchNodeCriticality,
  rejectEdge,
  resetNode,
  resetNodeCriticality,
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

const WORKLOAD_ID = "demo";

const WorkloadView: React.FC = () => {
  const [graph, setGraph] = useState<GraphSnapshot | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<EdgeData | null>(null);
  const [selectedNode, setSelectedNode] = useState<NodeData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewLevel, setViewLevel] = useState<ViewLevel>("overview");
  const [showLegend, setShowLegend] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState(320);
  const [isResizing, setIsResizing] = useState(false);

  // Initialize aiEnabled from URL query param
  const [aiLayerEnabled, setAiLayerEnabled] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get("ai") === "true";
  });
  const [userLayerEnabled, setUserLayerEnabled] = useState(true);
  const [serviceFilter, setServiceFilter] = useState<Set<string>>(new Set());
  const [resourceGroupFilter, setResourceGroupFilter] = useState<Set<string>>(new Set());
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());
  const [criticalityOverrides, setCriticalityOverrides] = useState<Map<string, number>>(new Map());

  const fetchGraph = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      const raw = await fetchWorkloadGraph(WORKLOAD_ID, aiLayerEnabled);
      const normalized = normalizeGraph(raw);
      setGraph(normalized);

      // Load persisted criticality overrides from backend node metadata.
      const persisted = new Map<string, number>();
      (normalized.nodes ?? []).forEach(n => {
        const meta = (n as any)?.metadata ?? {};
        const v = meta.criticality_override;
        if (typeof v === "number") persisted.set(n.id, v);
      });
      setCriticalityOverrides(persisted);
    } catch (err: any) {
      setError(err.message ?? "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [aiLayerEnabled]);

  // Update URL when aiLayerEnabled changes
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (aiLayerEnabled) {
        params.set("ai", "true");
    } else {
        params.delete("ai");
    }
    window.history.replaceState(null, "", `?${params.toString()}`);
  }, [aiLayerEnabled]);

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
      setServiceFilter(new Set(allServices));
    }
  }, [serviceOptions, serviceFilter.size]);

  useEffect(() => {
    if (!resourceGroupOptions.length) {
      if (resourceGroupFilter.size) setResourceGroupFilter(new Set());
      return;
    }

    const optionKeys = new Set(resourceGroupOptions.map(opt => opt.key));

    setResourceGroupFilter(prev => {
      if (prev.size === 0) {
        return new Set(optionKeys);
      }

      const next = new Set([...prev].filter(key => optionKeys.has(key)));
      if (next.size === 0) {
        return new Set(optionKeys);
      }

      const unchanged = next.size === prev.size && [...next].every(key => prev.has(key));
      if (unchanged && optionKeys.size === prev.size && [...optionKeys].every(key => prev.has(key))) {
        return prev;
      }

      return next;
    });
  }, [resourceGroupOptions, resourceGroupFilter.size]);

  const viewGraph = useMemo(() => {
    if (!graph) return null;
    return buildViewGraph({
      snapshot: graph,
      aiLayerEnabled,
      userLayerEnabled,
      serviceFilter,
      resourceGroupFilter,
      criticalityOverrides,
    });
  }, [graph, aiLayerEnabled, userLayerEnabled, serviceFilter, resourceGroupFilter, criticalityOverrides]);

  // Fetch workload graph
  useEffect(() => {
    fetchGraph();
  }, [fetchGraph]);

  // Accept edge
  const handleAcceptEdge = async (edgeId: string) => {
    try {
      await acceptEdge(WORKLOAD_ID, edgeId);

      // Optimistic UI update
      setGraph(prev =>
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
      await rejectEdge(WORKLOAD_ID, edgeId);

      setGraph(prev =>
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
      await deleteEdge(WORKLOAD_ID, edgeId);

      setGraph(prev =>
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

  const handleNodeSelected = (nodeId: string | null) => {
    if (!nodeId) {
      setSelectedNode(null);
      setSelectedEdge(null);
      return;
    }
    if (!viewGraph) return;

    const node = viewGraph.nodes.find(n => n.id === nodeId);
    if (!node) return;
    const meta = (node.metadata as any) ?? {};
    const effectiveCriticality = meta.criticality_score as number | undefined;
    const isCriticalityOverride = criticalityOverrides.has(node.id);
    setSelectedNode({
      id: node.id,
      name: node.name,
      type: node.type,
      layer: meta.importance as number | undefined,
      color: meta.color_override as string | undefined,
      icon: (meta.icon_override as string | undefined) ?? (meta.icon as string | undefined),
      override: meta.override as boolean | undefined,
      criticalityScore: effectiveCriticality,
      criticalityOverride: isCriticalityOverride,
      aiAnnotation: node.metadata?.ai_annotation as any,
      originalName: meta.original_name as string | undefined,
      raw: node,
    });
    setSelectedEdge(null);
  };

  const handleDragCreateLink = async (sourceId: string, targetId: string) => {
    await handleCreateManualLink(sourceId, targetId);
  };

  const handleCreateManualLink = async (fromId: string, toId: string) => {
    if (!fromId || !toId || fromId === toId) return;

    const relationship = "depends_on"; // Default relationship for drag-and-drop

    const optimisticId = `manual-pending-${fromId}-${relationship}-${toId}-${Date.now()}`;
    const optimisticEdge: GraphEdge = {
      id: optimisticId,
      source: fromId,
      target: toId,
      relationship,
      confidence: 1,
      status: "accepted",
      origin: "manual",
    };

    setGraph(prev =>
      prev
        ? {
            ...prev,
            edges: [...prev.edges, optimisticEdge],
          }
        : prev
    );

    try {
      const body = await createManualEdge(WORKLOAD_ID, {
        from_id: fromId,
        to_id: toId,
        relationship,
      });
      const created = body.edge;

      const newEdge: GraphEdge = {
        id: created?.id ?? `manual-${fromId}-${relationship}-${toId}-${Date.now()}`,
        source: created?.from_id ?? fromId,
        target: created?.to_id ?? toId,
        relationship: created?.relationship ?? relationship,
        confidence: created?.confidence ?? 1,
        status: created?.status ?? "accepted",
        origin: created?.source ?? "manual"
      };

      setGraph(prev =>
        prev
          ? {
              ...prev,
              edges: [
                ...prev.edges.filter(e => e.id !== optimisticId && e.id !== newEdge.id),
                newEdge
              ]
            }
          : prev
      );

    } catch (err: any) {
      console.error("Failed to create link:", err.message);

      setGraph(prev =>
        prev
          ? {
              ...prev,
              edges: prev.edges.filter(e => e.id !== optimisticId),
            }
          : prev
      );
    }
  };

  const handleRenameNode = async (nodeId: string) => {
    const node = viewGraph?.nodes.find(n => n.id === nodeId);
    if (!node) return;
    const meta = (node.metadata as any) ?? {};
    const effectiveCriticality = meta.criticality_score as number | undefined;
    const isCriticalityOverride = criticalityOverrides.has(node.id);
    setSelectedNode({
      id: node.id,
      name: node.name,
      type: node.type,
      layer: meta.importance as number | undefined,
      color: meta.color_override as string | undefined,
      icon: (meta.icon_override as string | undefined) ?? (meta.icon as string | undefined),
      override: meta.override as boolean | undefined,
      criticalityScore: effectiveCriticality,
      criticalityOverride: isCriticalityOverride,
      aiAnnotation: node.metadata?.ai_annotation as any,
      originalName: meta.original_name as string | undefined,
    });
  };

  const handleSaveNode = async (nodeId: string, payload: { name?: string; layer?: number | null; color?: string | null; icon?: string | null; criticality?: number | null }) => {
    try {
      const nodePatch: Parameters<typeof patchNode>[2] = {
        name: payload.name,
        layer: payload.layer,
        color: payload.color,
        icon: payload.icon,
      };

      const shouldPatchNode =
        payload.name !== undefined ||
        payload.layer !== undefined ||
        payload.color !== undefined ||
        payload.icon !== undefined;

      if (shouldPatchNode) {
        await patchNode(WORKLOAD_ID, nodeId, nodePatch);
      }

      if (payload.criticality === null) {
        if (criticalityOverrides.has(nodeId)) {
          await resetNodeCriticality(WORKLOAD_ID, nodeId);
        }
      } else if (typeof payload.criticality === "number") {
        await patchNodeCriticality(WORKLOAD_ID, nodeId, payload.criticality);
      }

      setGraph(prev =>
        prev
          ? {
              ...prev,
              nodes: prev.nodes.map(n =>
                n.id === nodeId
                  ? {
                      ...n,
                      name: payload.name ?? n.name,
                      metadata: {
                        ...n.metadata,
                        importance: payload.layer === undefined ? n.metadata?.importance : payload.layer,
                        color_override: payload.color === undefined ? n.metadata?.color_override : payload.color,
                        icon_override: payload.icon === undefined ? (n.metadata as any)?.icon_override : payload.icon,
                        icon: payload.icon === undefined ? (n.metadata as any)?.icon : payload.icon,
                        override: true
                      }
                    }
                  : n
              )
            }
          : prev
      );

      if (payload.criticality === null) {
        setCriticalityOverrides(prev => {
          if (!prev.has(nodeId)) return prev;
          const next = new Map(prev);
          next.delete(nodeId);
          return next;
        });
      } else if (typeof payload.criticality === "number") {
        setCriticalityOverrides(prev => {
          const next = new Map(prev);
          next.set(nodeId, payload.criticality as number);
          return next;
        });
      }

      setSelectedNode(prev => prev && prev.id === nodeId
        ? {
            ...prev,
            name: payload.name ?? prev.name,
            layer: payload.layer === undefined ? prev.layer : payload.layer ?? undefined,
            color: payload.color === undefined ? prev.color : payload.color ?? undefined,
            icon: payload.icon === undefined ? prev.icon : payload.icon ?? undefined,
            override: true,
            criticalityScore: payload.criticality === undefined
              ? prev.criticalityScore
              : (payload.criticality === null ? undefined : payload.criticality),
            criticalityOverride: payload.criticality === undefined
              ? prev.criticalityOverride
              : payload.criticality !== null,
          }
        : prev
      );
    } catch (err) {
      console.error("Failed to update node", err);
    }
  };

  const handleResetNode = async (nodeId: string) => {
    try {
      await resetNode(WORKLOAD_ID, nodeId);

      // Reset criticality override too (if present).
      if (criticalityOverrides.has(nodeId)) {
        await resetNodeCriticality(WORKLOAD_ID, nodeId);
        setCriticalityOverrides(prev => {
          const next = new Map(prev);
          next.delete(nodeId);
          return next;
        });
      }

      await fetchGraph();
      setSelectedNode(null);
    } catch (err) {
      console.error("Failed to reset node", err);
    }
  };

  // Always respect the view level selection
  const maxImportance = LEVEL_TO_MAX_IMPORTANCE[viewLevel];

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

  if (!graph) {
    return null;
  }

  const nodesForView = viewGraph?.nodes ?? graph.nodes;
  const edgesForView = viewGraph?.edges ?? graph.edges;

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
          <h2 style={{ margin: 0, fontSize: 16, color: "#eee", flex: 1 }}>Workload Graph</h2>
        </div>

        {/* Graph canvas */}
        <div style={{ flex: 1, height: "100%" }}>
          <ReactFlowProvider>
            <GraphCanvas
              nodes={nodesForView}
              edges={edgesForView}
              maxImportance={maxImportance}
              onNodeSelected={handleNodeSelected}
              onEdgeCreate={handleDragCreateLink}
              onNodeRename={handleRenameNode}
              onEdgeSelected={(e) => {
                if (!e) return setSelectedEdge(null);

                setSelectedEdge({
                  id: e.id,
                  source: e.source,
                  target: e.target,
                  relationship: e.relationship,
                  confidence: e.confidence,
                  status: e.status as any,
                  origin: e.origin,
                  raw: e,
                });
              }}
            />
          </ReactFlowProvider>
        </div>
      </div>

      {/* Right drawer */}
      {selectedEdge && (
        <EdgeDrawer
          edge={selectedEdge}
          onAccept={handleAcceptEdge}
          onReject={handleRejectEdge}
          onDelete={handleDeleteEdge}
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