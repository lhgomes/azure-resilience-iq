import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ReactFlowProvider } from "reactflow";
import GraphCanvas, {
  GraphNode,
  GraphEdge,
  GraphCanvasHandle
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
  reverseEdgeDirection,
  fetchWorkloadGraph,
  patchNode,
  rejectEdge,
  resetNode,
  createGroup,
  updateGroup,
  deleteGroup,
  addNodeToGroup,
  removeNodeFromGroup,
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
const STORAGE_KEY = `workload_graph_${WORKLOAD_ID}`;

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

  const lastSuggestedGroupNameRef = useRef<string>("");
  const lastGroupToolbarSelectionRef = useRef(groupToolbarSelection);
  const graphCanvasRef = useRef<GraphCanvasHandle>(null);

  const readStoredGraph = (): GraphSnapshot | null => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
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
      localStorage.removeItem(STORAGE_KEY);
      return;
    }
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
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

  const fetchGraph = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      const raw = await fetchWorkloadGraph(WORKLOAD_ID);
      const normalized = normalizeGraph(raw);
      persistGraph(normalized);
      setGraph(normalized);
    } catch (err: any) {
      setError(err.message ?? "Unknown error");
    } finally {
      setLoading(false);
    }
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
      // If nothing selected yet, default to all available groups.
      if (prev.size === 0) {
        return new Set(optionKeys);
      }

      // Keep the user's current selection; avoid shrinking it when the option list changes.
      // This prevents transient option recalculation from hiding nodes unexpectedly.
      return prev;
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
    });
  }, [graph, aiLayerEnabled, userLayerEnabled, serviceFilter, resourceGroupFilter]);

  // Hydrate from local storage, then fetch fresh graph
  useEffect(() => {
    const stored = readStoredGraph();
    if (stored) {
      setGraph(stored);
    }

    fetchGraph();
  }, [fetchGraph]);

  // Accept edge
  const handleAcceptEdge = async (edgeId: string) => {
    try {
      await acceptEdge(WORKLOAD_ID, edgeId);

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
      await rejectEdge(WORKLOAD_ID, edgeId);

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
      await deleteEdge(WORKLOAD_ID, edgeId);

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
      const result = await reverseEdgeDirection(WORKLOAD_ID, edgeId);
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
      const body = await createManualEdge(WORKLOAD_ID, {
        source: sourceId,
        target: targetId,
        relationship: "depends_on",
      });
      const created = body.edge;

      if (created) {
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

      await patchNode(WORKLOAD_ID, nodeId, nodePatch);

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
            await removeNodeFromGroup(WORKLOAD_ID, group.id, nodeId);
          }
        }
      }
    }

    // Create the new group
    await createGroup(WORKLOAD_ID, { id: groupId, name: label, nodes: memberIds });
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
    await deleteGroup(WORKLOAD_ID, groupId);
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
    await updateGroup(WORKLOAD_ID, groupId, { name: label });
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
          await removeNodeFromGroup(WORKLOAD_ID, group.id, nodeId);
        }
      }
    }

    // Add to the new group
    await addNodeToGroup(WORKLOAD_ID, groupId, nodeId);
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
    await removeNodeFromGroup(WORKLOAD_ID, groupId, nodeId);
  };

  const handleNodeRemoveFromGroupClick = (args: { nodeId: string; groupId: string }) => {
    // Just call the handler directly - it's synchronous for UI feedback
    handleRemoveNodeFromGroup(args);
  };

  const handleResetNode = async (nodeId: string) => {
    try {
      await resetNode(WORKLOAD_ID, nodeId);

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
          <h2 style={{ margin: 0, fontSize: 16, color: "#eee", flex: 1 }}>Workload Graph</h2>
        </div>

        {/* Graph canvas */}
        <div style={{ flex: 1, height: "100%" }}>
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