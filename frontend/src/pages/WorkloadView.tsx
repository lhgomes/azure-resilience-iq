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

interface GraphSnapshot {
  nodes: GraphNode[];
  edges: GraphEdge[];
  llm_annotations?: {
    nodes?: LlmNodeAnnotation[];
    edges?: LlmEdgeSuggestion[];
  };
}

interface LlmNodeAnnotationPayload {
  display_name?: string;
  azure_service_category?: string;
  azure_service_name?: string;
  criticality_score?: number;
  layer?: number;
  priority?: string;
  hide_by_default?: boolean;
  confidence?: number;
  reason?: string;
  source?: string;
}

interface LlmNodeAnnotation {
  node_id: string;
  annotations: LlmNodeAnnotationPayload;
}

interface LlmEdgeSuggestion {
  from_id: string;
  to_id: string;
  relationship: string;
  confidence?: number;
  reason?: string;
  status?: string;
  source?: string;
}

type ViewLevel = "overview" | "network" | "full";

const LEVEL_TO_MAX_IMPORTANCE: Record<ViewLevel, number> = {
  overview: 1,
  network: 2,
  full: 3
};

const WORKLOAD_ID = "demo";

const normalizeTypeString = (rawType?: string): string => {
  const t = (rawType || "").trim().toLowerCase();
  if (!t) return "resource";
  if (t === "vm" || t === "virtualmachine" || t === "virtual_machine" || t === "virtual machine") return "vm";
  if (t.includes("virtualmachines") || t.includes("virtual-machine")) return "vm";
  if (t.includes("microsoft.compute") && t.includes("virtual")) return "vm";
  if (t === "aks" || t.includes("managedclusters")) return "aks";
  if (t === "vnet" || t.includes("virtualnetworks")) return "vnet";
  if (t === "subnet" || t.includes("subnets")) return "subnet";
  if (t === "nic" || t.includes("networkinterfaces")) return "nic";
  if (t === "nsg" || t.includes("networksecuritygroups")) return "nsg";
  if (t === "pip" || t.includes("publicipaddresses")) return "pip";
  if (t.includes("privateendpoints")) return "private_endpoint";
  if (t.includes("storageaccounts")) return "storage";
  if (t.includes("keyvault")) return "keyvault";
  if (t.includes("sql")) return "sql";
  if (t.includes("networkwatcher")) return "network";
  return t;
};

const canonicalTypeForNode = (n: GraphNode): string => {
  const meta = n.metadata as any;
  const candidates = [n.type, meta?.raw_type, meta?.resource_type, meta?.provider, meta?.azure_type, meta?.kind, meta?.type];

  for (const c of candidates) {
    const normalized = normalizeTypeString(c);
    if (normalized !== "resource") return normalized;
  }

  const haystack = `${n.id} ${n.name} ${meta?.original_name ?? ""}`.toLowerCase();
  if (
    haystack.includes("/virtualmachines/") ||
    haystack.includes(" virtual machine") ||
    haystack.includes(" virtualmachine") ||
    haystack.includes(" vm")
  ) {
    return "vm";
  }

  return "resource";
};

const renderStars = (score: number): string => {
  const fullStars = Math.floor(score / 2);
  const hasHalf = score % 2 === 1;
  let stars = "★".repeat(fullStars);
  if (hasHalf) stars += "⯪";
  stars += "☆".repeat(5 - fullStars - (hasHalf ? 1 : 0));
  return stars;
};

const getEffectiveCriticalityScore = (
  ann: LlmNodeAnnotationPayload | undefined,
  overrides: Map<string, number>,
  nodeId: string
): number | undefined => {
  const override = overrides.get(nodeId);
  if (override !== undefined) return override;
  return ann?.criticality_score;
};

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
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());
  const [criticalityOverrides, setCriticalityOverrides] = useState<Map<string, number>>(new Map());

  const fetchGraph = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      const res = await fetch(
        `/api/workloads/${WORKLOAD_ID}/graph${aiLayerEnabled ? "?include_llm=true" : ""}`
      );

      if (!res.ok) {
        throw new Error(`Failed to load graph (${res.status})`);
      }

      const data = await res.json();
      setGraph(normalizeGraph(data));
    } catch (err: any) {
      setError(err.message ?? "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [aiLayerEnabled]);

  const normalizeGraph = (raw: any): GraphSnapshot => {
    const edges = (raw?.edges ?? []).map((e: any) => ({
      ...e,
      source: (e as any).from_id ?? (e as any).source,
      target: (e as any).to_id ?? (e as any).target,
      origin: (e as any).origin ?? (e as any).source ?? "arg",
      status: e?.status ?? "proposed"
    }));

    return {
      nodes: raw?.nodes ?? [],
      edges,
      llm_annotations: raw?.llm_annotations
    };
  };

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

  const serviceOptions = useMemo(() => {
    if (!graph) return [];
    
    const annMap = new Map<string, LlmNodeAnnotationPayload>();
    (graph.llm_annotations?.nodes ?? []).forEach(entry => {
      if (entry?.node_id) {
        annMap.set(entry.node_id, entry.annotations || {});
      }
    });

    const maxImportance = LEVEL_TO_MAX_IMPORTANCE[viewLevel];

    // Group services by category, only including nodes visible at current view level
    const categoryMap = new Map<string, Map<string, string>>();
    
    (graph.nodes || []).forEach(n => {
      const key = canonicalTypeForNode(n);
      if (!key) return;
      
      const ann = annMap.get(n.id);
      
      // Calculate effective importance for this node
      const baseImportance = n.metadata?.original_importance ?? n.metadata?.importance ?? 3;
      let importance = baseImportance;
      
      // AI layer overlays on top of raw
      if (aiLayerEnabled && ann?.layer !== undefined) {
        importance = ann.layer;
      }
      
      // User layer overrides on top of AI/raw (only if explicitly overridden)
      if (userLayerEnabled && n.metadata?.override) {
        importance = n.metadata?.importance ?? importance;
      }
      
      // Only include nodes visible at current view level
      if (importance > maxImportance) return;
      
      const category = ann?.azure_service_category || "Other";
      const label = ann?.azure_service_name || key;
      
      if (!categoryMap.has(category)) {
        categoryMap.set(category, new Map());
      }
      
      const servicesInCategory = categoryMap.get(category)!;
      if (!servicesInCategory.has(key)) {
        servicesInCategory.set(key, label);
      }
    });

    // Convert to array structure with sorted categories and services
    return Array.from(categoryMap.entries())
      .map(([category, servicesMap]) => ({
        category,
        services: Array.from(servicesMap.entries())
          .map(([key, label]) => ({ key, label }))
          .sort((a, b) => a.label.localeCompare(b.label))
      }))
      .sort((a, b) => a.category.localeCompare(b.category));
  }, [graph, viewLevel, aiLayerEnabled, userLayerEnabled]);

  useEffect(() => {
    if (serviceOptions.length && serviceFilter.size === 0) {
      const allServices = serviceOptions.flatMap(cat => cat.services.map(s => s.key));
      setServiceFilter(new Set(allServices));
    }
  }, [serviceOptions, serviceFilter.size]);

  const viewGraph = useMemo(() => {
    if (!graph) return null;

    const annotationMap = new Map<string, LlmNodeAnnotationPayload>();

    if (aiLayerEnabled) {
      (graph.llm_annotations?.nodes ?? []).forEach(entry => {
        if (entry?.node_id) {
          annotationMap.set(entry.node_id, entry.annotations || {});
        }
      });
    }

    const visibleNodes = graph.nodes
      .map(n => {
        const ann = annotationMap.get(n.id);

        const normalizedType = canonicalTypeForNode(n);

        const baseName = n.metadata?.original_name ?? n.name;
        const baseImportance = n.metadata?.original_importance ?? n.metadata?.importance ?? 3;

        let name = baseName;
        let importance = baseImportance;

        // AI layer overlays on top of raw
        if (aiLayerEnabled) {
          if (ann?.display_name) name = ann.display_name;
          if (ann?.layer !== undefined) importance = ann.layer;
        }

        // User layer overrides on top of AI/raw (only if explicitly overridden)
        if (userLayerEnabled && n.metadata?.override) {
          name = n.name ?? name;
          importance = n.metadata?.importance ?? importance;
        }

        const escapeHtml = (text: string) =>
          text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\"/g, "&quot;")
            .replace(/'/g, "&#39;");

        const tooltip = ann && aiLayerEnabled
          ? [
              "<strong>AI suggestion</strong><br>",
              ann.display_name ? `Name: <strong>${escapeHtml(ann.display_name)}</strong>` : undefined,
              baseName ? `Original: <strong>${escapeHtml(baseName)}</strong>` : undefined,
              ann.priority ? `Priority: <strong>${escapeHtml(ann.priority)}</strong>` : undefined,
              ann.criticality_score !== undefined ? `Criticality: <strong>${ann.criticality_score}/10</strong>` : undefined,
              ann.confidence !== undefined ? `Confidence: <strong>${Math.round((ann.confidence ?? 0) * 100)}%</strong>` : undefined,
              ann.reason ? `Reason: <strong>${escapeHtml(ann.reason)}</strong>` : undefined
            ].filter(Boolean).join("\n")
          : undefined;

        return {
          ...n,
          type: normalizedType,
          name,
          metadata: {
            ...n.metadata,
            importance,
            ai_annotation: ann,
            original_name: baseName,
            raw_type: n.type,
            ai_tooltip: tooltip,
            criticality_score: getEffectiveCriticalityScore(ann, criticalityOverrides, n.id),
            criticality_stars: renderStars(getEffectiveCriticalityScore(ann, criticalityOverrides, n.id) ?? 5)
          } as Record<string, any>,
        };
      })
      .filter(n => serviceFilter.size === 0 || serviceFilter.has((n as any).type));

    const visibleIds = new Set(visibleNodes.map(n => n.id));

    const baseEdges = graph.edges
      .filter(e => !(!userLayerEnabled && (e as any).origin === "manual"))
      .filter(e => visibleIds.has(e.source) && visibleIds.has(e.target));

    const existingKeys = new Set(
      baseEdges.map(e => `${e.source}|${e.relationship}|${e.target}`)
    );

    const suggestedEdges: GraphEdge[] = aiLayerEnabled
      ? (graph.llm_annotations?.edges ?? [])
          .map(s => ({
            id: `llm-${s.from_id}-${s.relationship}-${s.to_id}`,
            source: s.from_id,
            target: s.to_id,
            relationship: s.relationship,
            confidence: s.confidence ?? 0.5,
            status: "proposed", // keep AI suggestions advisory-only
            origin: s.source ?? "llm",
          }))
          .filter(e =>
            !existingKeys.has(`${e.source}|${e.relationship}|${e.target}`) &&
            visibleIds.has(e.source) &&
            visibleIds.has(e.target)
          )
      : [];

    return {
      nodes: visibleNodes,
      edges: [...baseEdges, ...suggestedEdges],
      annotationMap,
    };
  }, [graph, aiLayerEnabled, userLayerEnabled, serviceFilter, criticalityOverrides]);

  // Fetch workload graph
  useEffect(() => {
    fetchGraph();
  }, [fetchGraph]);

  // Accept edge
  const handleAcceptEdge = async (edgeId: string) => {
    try {
      await fetch(
        `/api/workloads/${WORKLOAD_ID}/edges/${edgeId}/accept`,
        { method: "POST" }
      );

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
      await fetch(
        `/api/workloads/${WORKLOAD_ID}/edges/${edgeId}/reject`,
        { method: "POST" }
      );

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
      const res = await fetch(
        `/api/workloads/${WORKLOAD_ID}/edges/${edgeId}`,
        { method: "DELETE" }
      );

      if (!res.ok) {
        throw new Error(`Failed to delete link (${res.status})`);
      }

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

  const handleNodeSelected = async (nodeId: string | null) => {
    if (!nodeId || !viewGraph) return;

    const node = viewGraph.nodes.find(n => n.id === nodeId);
    if (!node) return;
    const effectiveCriticality = node.metadata?.criticality_score;
    const isCriticalityOverride = criticalityOverrides.has(node.id);
    setSelectedNode({
      id: node.id,
      name: node.name,
      type: node.type,
      layer: node.metadata?.importance,
      shape: node.metadata?.shape_override,
      color: node.metadata?.color_override,
      override: node.metadata?.override,
      criticalityScore: effectiveCriticality,
      criticalityOverride: isCriticalityOverride,
      aiAnnotation: node.metadata?.ai_annotation as LlmNodeAnnotationPayload | undefined,
      originalName: node.metadata?.original_name,
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

    try {
      const res = await fetch(
        `/api/workloads/${WORKLOAD_ID}/edges`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            from_id: fromId,
            to_id: toId,
            relationship
          })
        }
      );

      if (!res.ok) {
        throw new Error(`Failed to create link (${res.status})`);
      }

      const body = await res.json();
      const created = body.edge || {};

      const newEdge: GraphEdge = {
        id: created.id,
        source: created.from_id ?? created.source ?? fromId,
        target: created.to_id ?? created.target ?? toId,
        relationship: created.relationship ?? relationship,
        confidence: created.confidence ?? 1,
        status: created.status ?? "accepted",
        origin: created.source ?? "manual"
      };

      setGraph(prev =>
        prev
          ? {
              ...prev,
              edges: [
                ...prev.edges.filter(e => e.id !== newEdge.id),
                newEdge
              ]
            }
          : prev
      );

    } catch (err: any) {
      console.error("Failed to create link:", err.message);
    }
  };

  const handleRenameNode = async (nodeId: string) => {
    const node = viewGraph?.nodes.find(n => n.id === nodeId);
    if (!node) return;
    const effectiveCriticality = node.metadata?.criticality_score;
    const isCriticalityOverride = criticalityOverrides.has(node.id);
    setSelectedNode({
      id: node.id,
      name: node.name,
      type: node.type,
      layer: node.metadata?.importance,
      shape: node.metadata?.shape_override,
      color: node.metadata?.color_override,
      override: node.metadata?.override,
      criticalityScore: effectiveCriticality,
      criticalityOverride: isCriticalityOverride,
      aiAnnotation: node.metadata?.ai_annotation as LlmNodeAnnotationPayload | undefined,
      originalName: node.metadata?.original_name,
    });
  };

  const handleSaveNode = async (nodeId: string, payload: { name?: string; layer?: number | null; shape?: string | null; color?: string | null; }) => {
    try {
      const res = await fetch(`/api/workloads/${WORKLOAD_ID}/nodes/${encodeURIComponent(nodeId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      if (!res.ok) {
        throw new Error(`Failed to update node (${res.status})`);
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
                        shape_override: payload.shape === undefined ? n.metadata?.shape_override : payload.shape,
                        color_override: payload.color === undefined ? n.metadata?.color_override : payload.color,
                        override: true
                      }
                    }
                  : n
              )
            }
          : prev
      );

      setSelectedNode(prev => prev && prev.id === nodeId
        ? {
            ...prev,
            name: payload.name ?? prev.name,
            layer: payload.layer === undefined ? prev.layer : payload.layer ?? undefined,
            shape: payload.shape === undefined ? prev.shape : payload.shape ?? undefined,
            color: payload.color === undefined ? prev.color : payload.color ?? undefined,
            override: true,
          }
        : prev
      );
    } catch (err) {
      console.error("Failed to update node", err);
    }
  };

  const handleResetNode = async (nodeId: string) => {
    try {
      const res = await fetch(
        `/api/workloads/${WORKLOAD_ID}/nodes/${nodeId}`,
        { method: "DELETE" }
      );

      if (!res.ok) {
        throw new Error(`Failed to reset node (${res.status})`);
      }

      await fetchGraph();
      setSelectedNode(null);
    } catch (err) {
      console.error("Failed to reset node", err);
    }
  };

  const handleSaveCriticalityScore = async (nodeId: string, score: number) => {
    try {
      const res = await fetch(
        `/api/workloads/${WORKLOAD_ID}/nodes/${encodeURIComponent(nodeId)}/criticality`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ score })
        }
      );

      if (!res.ok) {
        throw new Error(`Failed to update criticality score (${res.status})`);
      }

      const newOverrides = new Map(criticalityOverrides);
      newOverrides.set(nodeId, score);
      setCriticalityOverrides(newOverrides);

      setSelectedNode(prev => prev && prev.id === nodeId
        ? { ...prev, criticalityScore: score, criticalityOverride: true }
        : prev
      );
    } catch (err) {
      console.error("Failed to save criticality score", err);
    }
  };

  const handleResetCriticalityScore = async (nodeId: string) => {
    try {
      const res = await fetch(
        `/api/workloads/${WORKLOAD_ID}/nodes/${encodeURIComponent(nodeId)}/criticality`,
        { method: "DELETE" }
      );

      if (!res.ok) {
        throw new Error(`Failed to reset criticality score (${res.status})`);
      }

      const newOverrides = new Map(criticalityOverrides);
      newOverrides.delete(nodeId);
      setCriticalityOverrides(newOverrides);

      setSelectedNode(prev => prev && prev.id === nodeId
        ? { ...prev, criticalityScore: undefined, criticalityOverride: false }
        : prev
      );
    } catch (err) {
      console.error("Failed to reset criticality score", err);
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
          <div
            style={{
              padding: 16,
              overflowY: "auto",
              overflowX: "hidden",
              flex: 1
            }}
          >
            <h3 style={{ margin: "0 0 20px 0", color: "#eee", fontSize: 16 }}>Controls</h3>

          {/* View Level */}
          <div style={{ marginBottom: 20 }}>
            <label style={{ display: "block", fontSize: 12, color: "#9AA0A6", marginBottom: 8 }}>
              View Level
            </label>
            <select
              value={viewLevel}
              onChange={e => setViewLevel(e.target.value as ViewLevel)}
              style={{
                width: "100%",
                background: "#181818",
                color: "#fff",
                border: "1px solid #333",
                padding: "8px",
                borderRadius: 4
              }}
            >
              <option value="overview">Overview (L0)</option>
              <option value="network">Network (L1)</option>
              <option value="full">Full (L2)</option>
            </select>
          </div>

          {/* Toggles */}
          <div style={{ marginBottom: 20 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 8, color: "#eee", fontSize: 13, marginBottom: 8, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={aiLayerEnabled}
                onChange={e => setAiLayerEnabled(e.target.checked)}
              />
              AI layer
            </label>

            <label style={{ display: "flex", alignItems: "center", gap: 8, color: "#eee", fontSize: 13, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={userLayerEnabled}
                onChange={e => setUserLayerEnabled(e.target.checked)}
              />
              User overrides
            </label>
          </div>

          {/* Legend Button */}
          <button
            onClick={() => setShowLegend(prev => !prev)}
            title="Show/hide visual legend"
            style={{
              width: "100%",
              padding: "8px 12px",
              background: showLegend ? "#1a1a2e" : "#161616",
              color: showLegend ? "#f59e0b" : "#9AA0A6",
              border: showLegend ? "1px solid #f59e0b" : "1px solid #333",
              borderRadius: 4,
              cursor: "pointer",
              fontSize: 13,
              marginBottom: 20
            }}
          >
            ? Legend
          </button>

          {/* Services Filter */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <label style={{ fontSize: 12, color: "#9AA0A6" }}>
                Services
              </label>
              <button
                onClick={() => {
                  const allServices = serviceOptions.flatMap(cat => cat.services.map(s => s.key));
                  setServiceFilter(new Set(allServices));
                }}
                style={{
                  background: "transparent",
                  border: "none",
                  color: "#85a2c6ff",
                  cursor: "pointer",
                  fontSize: 11,
                  textDecoration: "underline"
                }}
              >
                Select All
              </button>
            </div>
            <div style={{ maxHeight: 400, overflowY: "auto", border: "1px solid #333", borderRadius: 4, padding: 8, background: "#181818" }}>
              {serviceOptions.map(category => {
                const allServicesInCategory = category.services.map(s => s.key);
                const selectedServicesInCategory = allServicesInCategory.filter(key => serviceFilter.has(key));
                const isExpanded = expandedCategories.has(category.category);
                const allSelected = selectedServicesInCategory.length === allServicesInCategory.length;
                const someSelected = selectedServicesInCategory.length > 0 && !allSelected;

                return (
                  <div key={category.category} style={{ marginBottom: 8 }}>
                    {/* Category Header */}
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                      <button
                        onClick={() => {
                          const newExpanded = new Set(expandedCategories);
                          if (isExpanded) {
                            newExpanded.delete(category.category);
                          } else {
                            newExpanded.add(category.category);
                          }
                          setExpandedCategories(newExpanded);
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
                          justifyContent: "center"
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
                          const newFilter = new Set(serviceFilter);
                          if (e.target.checked) {
                            allServicesInCategory.forEach(key => newFilter.add(key));
                          } else {
                            allServicesInCategory.forEach(key => newFilter.delete(key));
                          }
                          setServiceFilter(newFilter);
                        }}
                        style={{ cursor: "pointer" }}
                      />
                      <span
                        style={{
                          fontSize: 13,
                          fontWeight: 600,
                          color: "#eee",
                          cursor: "pointer"
                        }}
                        onClick={() => {
                          const newExpanded = new Set(expandedCategories);
                          if (isExpanded) {
                            newExpanded.delete(category.category);
                          } else {
                            newExpanded.add(category.category);
                          }
                          setExpandedCategories(newExpanded);
                        }}
                      >
                        {category.category} ({category.services.length})
                      </span>
                    </div>

                    {/* Services in Category */}
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
                              color: "#ddd"
                            }}
                          >
                            <input
                              type="checkbox"
                              checked={serviceFilter.has(service.key)}
                              onChange={e => {
                                const newFilter = new Set(serviceFilter);
                                if (e.target.checked) {
                                  newFilter.add(service.key);
                                } else {
                                  newFilter.delete(service.key);
                                }
                                setServiceFilter(newFilter);
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
        </div>
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
                  source: e.source ?? (e as any).from_id,
                  target: e.target ?? (e as any).to_id,
                  relationship: e.relationship,
                  confidence: e.confidence,
                status: (e.status as any),
                origin: (e as any).origin,
                raw: e
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
          onDelete={handleDeleteEdge}
          onClose={() => setSelectedEdge(null)}
        />
      )}

      {selectedNode && (
        <NodeDrawer
          node={selectedNode}
          onClose={() => setSelectedNode(null)}
          onSave={handleSaveNode}
          onSaveCriticality={handleSaveCriticalityScore}
          onResetCriticality={() => handleResetCriticalityScore(selectedNode.id)}
          onReset={() => handleResetNode(selectedNode.id)}
        />
      )}

      {/* Legend panel */}
      {showLegend && (
        <div
          style={{
            position: "fixed",
            top: 60,
            right: 20,
            width: 280,
            background: "#111",
            border: "1px solid #333",
            borderRadius: 6,
            padding: 16,
            color: "#eee",
            zIndex: 100,
            boxShadow: "0 4px 12px rgba(0,0,0,0.5)"
          }}
        >
          <div style={{ marginBottom: 16 }}>
            <h4 style={{ marginTop: 0, marginBottom: 8 }}>Visual Legend</h4>
            <button
              onClick={() => setShowLegend(false)}
              style={{
                position: "absolute",
                top: 8,
                right: 8,
                background: "transparent",
                border: "none",
                color: "#9AA0A6",
                cursor: "pointer",
                fontSize: 16
              }}
            >
              ✕
            </button>
          </div>

          <div style={{ fontSize: 12, lineHeight: 1.6 }}>
            <div style={{ marginBottom: 12 }}>
              <strong>Node colors:</strong>
              <div style={{ marginTop: 4, color: "#9AA0A6" }}>
                <div style={{ marginBottom: 3 }}><span style={{ color: "#0078D4" }}>⬤ Compute (AKS, VM)</span></div>
                <div style={{ marginBottom: 3 }}><span style={{ color: "#85a2c6ff" }}>⬤ Network (VNet, Subnet)</span></div>
                <div style={{ marginBottom: 3 }}><span style={{ color: "#00B294" }}>⬤ PaaS (SQL, Storage, KeyVault)</span></div>
                <div style={{ marginBottom: 3 }}><span style={{ color: "#16a34a" }}>⬤ Manual (user-created)</span></div>
              </div>
            </div>

            <div style={{ marginBottom: 12 }}>
              <strong>Edge styles:</strong>
              <div style={{ marginTop: 4, color: "#9AA0A6" }}>
                <div style={{ marginBottom: 3 }}><span style={{ color: "#0078D4" }}>━ Solid blue: ARG (Azure)</span></div>
                <div style={{ marginBottom: 3 }}><span style={{ color: "#16a34a" }}>━ Solid green: Manual</span></div>
                <div style={{ marginBottom: 3 }}>╌ Dashed: Heuristic</div>
              </div>
            </div>

            <div style={{ marginBottom: 12 }}>
              <strong>View levels:</strong>
              <div style={{ marginTop: 4, color: "#9AA0A6" }}>
                <div style={{ marginBottom: 3 }}>L0: Core workload</div>
                <div style={{ marginBottom: 3 }}>L1: Network + Platform</div>
                <div style={{ marginBottom: 3 }}>L2: Full + Implementation</div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default WorkloadView;