import React, { useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, forwardRef } from "react";
import ReactFlow, {
  Node,
  Edge,
  useNodesState,
  useEdgesState,
  Controls,
  useReactFlow,
  MarkerType,
  NodeTypes,
  EdgeTypes,
  Connection,
  ConnectionMode,
  OnSelectionChangeParams,
  NodeDragHandler,
} from "reactflow";
import "reactflow/dist/style.css";
import dagre from "dagre";
import AzureNode from "./AzureNode";
import AzureEdge from "./AzureEdge";
import AzureGroupNode from "./AzureGroupNode";

export interface GraphNode {
  id: string;
  name: string;
  type: string;
  metadata?: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  relationship: string;
  confidence?: number;
  status?: "proposed" | "accepted" | "rejected";
  evidence?: Array<Record<string, unknown>>;
  origin?: string;
}

type GroupCreateRequest = { nonce: number; label: string };

type GroupRect = {
  x: number;
  y: number;
  width: number;
  height: number;
};

type GroupState = {
  id: string;
  label: string;
  memberIds: string[];
  collapsed: boolean;
  rect: GroupRect;
  childPositions: Record<string, { x: number; y: number }>;
};

// Layout / sizing constants.
const NODE_W = 180;
const NODE_H = 200;
const GROUP_PAD = 40;
const COLLAPSED_GROUP_W = 220;
const COLLAPSED_GROUP_H = 72;

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
  userLayerEnabled: boolean;
  selectedEdgeId?: string | null;
  onEdgeSelected?: (edge: GraphEdge | null) => void;
  onNodeSelected?: (nodeId: string | null) => void;
  maxImportance?: number;
  onEdgeCreate?: (sourceId: string, targetId: string) => void;
  onNodeRename?: (nodeId: string) => void;

  onGroupCreate?: (args: { groupId: string; label: string; memberIds: string[] }) => Promise<void> | void;
  groupCreateRequest?: GroupCreateRequest | null;
  onMoveNodeToGroup?: (args: { nodeId: string; groupId: string }) => Promise<void> | void;
  onRemoveNodeFromGroup?: (args: { nodeId: string; groupId: string }) => Promise<void> | void;
  onNodeRemoveFromGroup?: (args: { nodeId: string; groupId: string }) => void;
  onSelectionStateChange?: (state: {
    selectedNodeIds: string[];
    selectedGroupId: string | null;
    selectedGroupLabel?: string;
    selectedGroupMemberIds?: string[];
  }) => void;
}

const nodeTypes: NodeTypes = { azure: AzureNode };
const edgeTypes: EdgeTypes = { azure: AzureEdge };
const nodeTypesWithGroups: NodeTypes = { ...nodeTypes, azureGroup: AzureGroupNode };

export interface GraphCanvasHandle {
  fitView: () => void;
}

const GraphCanvas = forwardRef<GraphCanvasHandle, Props>((props, ref) => {
  const {
    nodes: nodesProp,
    edges: edgesProp,
    userLayerEnabled,
    onEdgeSelected,
    onNodeSelected,
    maxImportance = 1,
    onEdgeCreate,
    onNodeRename,
    onGroupCreate,
    groupCreateRequest,
    onMoveNodeToGroup,
    onRemoveNodeFromGroup,
    onNodeRemoveFromGroup,
    onSelectionStateChange,
    selectedEdgeId = null,
  } = props;
  
  const { fitView } = useReactFlow();

  // Expose fitView to parent via ref
  useImperativeHandle(ref, () => ({
    fitView: () => {
      setTimeout(() => fitView(), 100);
    }
  }), [fitView]);

  const [groups, setGroups] = useState<GroupState[]>([]);
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [groupBusy, setGroupBusy] = useState(false);
  const lastCreateNonceRef = useRef<number | null>(null);
  const [pendingGroupByNodeId, setPendingGroupByNodeId] = useState<
    Record<string, { groupId: string; label?: string | null }>
  >({});

  // Filter nodes by importance
  const visibleNodes = useMemo(() => {
    return nodesProp.filter(n => {
      const meta = n.metadata ?? {};
      const importance = typeof meta["importance"] === "number" ? (meta["importance"] as number) : 3;
      return importance <= maxImportance;
    });
  }, [nodesProp, maxImportance]);

  const visibleNodeIds = useMemo(() => new Set(visibleNodes.map(n => n.id)), [visibleNodes]);

  // Merge backend-provided grouping with local pending moves (for immediate UX after drag/drop).
  const groupInfoByNodeId = useMemo(() => {
    const map = new Map<string, { groupId: string; label: string | null }>();
    for (const n of visibleNodes) {
      const meta = (n.metadata ?? {}) as Record<string, unknown>;
      const groupId = typeof meta["group_id"] === "string" ? (meta["group_id"] as string) : null;
      if (!groupId) continue;
      const groupLabel = typeof meta["group_label"] === "string" ? (meta["group_label"] as string) : null;
      map.set(n.id, { groupId, label: groupLabel });
    }

    for (const [nodeId, pending] of Object.entries(pendingGroupByNodeId)) {
      if (!visibleNodeIds.has(nodeId)) continue;
      map.set(nodeId, { groupId: pending.groupId, label: pending.label ?? null });
    }

    return map;
  }, [visibleNodes, pendingGroupByNodeId, visibleNodeIds]);

  useEffect(() => {
    // Reconcile pending moves when the upstream node metadata matches.
    setPendingGroupByNodeId(prev => {
      let changed = false;
      const next: typeof prev = { ...prev };

      for (const [nodeId, pending] of Object.entries(prev)) {
        const n = nodesProp.find(x => x.id === nodeId);
        const meta = (n?.metadata ?? {}) as Record<string, unknown>;
        const gid = typeof meta["group_id"] === "string" ? (meta["group_id"] as string) : null;
        if (gid === pending.groupId) {
          delete next[nodeId];
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [nodesProp]);

  const visibleEdges = useMemo(() => {
    return edgesProp.filter(e => visibleNodeIds.has(e.source) && visibleNodeIds.has(e.target));
  }, [edgesProp, visibleNodeIds]);

  // Convert to Reactflow format
  const rfNodes: Node[] = useMemo(() => {
    return visibleNodes.map(n => ({
      id: n.id,
      data: (() => {
        const meta = n.metadata ?? {};
        const userOverride = (meta["user_override"] as Record<string, unknown> | undefined) ?? undefined;
        const isUserCustomized = userLayerEnabled && !!userOverride && Object.keys(userOverride).length > 0;
        return {
          label: n.name || n.id.split("/").pop() || "unknown",
          icon: typeof meta["icon"] === "string" ? (meta["icon"] as string) : undefined,
          type: n.type,
          criticality_stars:
            typeof meta["criticality_stars"] === "string" ? (meta["criticality_stars"] as string) : undefined,
          criticality_score: typeof meta["criticality_score"] === "number" ? (meta["criticality_score"] as number) : undefined,
          confidence: typeof meta["confidence"] === "number" ? (meta["confidence"] as number) : undefined,
          color: typeof meta["color"] === "string" ? (meta["color"] as string) : undefined,
          azure_service_category: typeof meta["azure_service_category"] === "string" ? (meta["azure_service_category"] as string) : undefined,
          ai_annotation: !!meta["ai_annotation"],
          user_customized: isUserCustomized,
          ai_tooltip: meta["ai_tooltip"],
          user_tooltip: meta["user_tooltip"],
          metadata: meta,
        };
      })(),
      type: "azure",
      position: { x: 0, y: 0 },
      connectable: true,
    }));
  }, [visibleNodes, userLayerEnabled]);

  const rfEdges: Edge[] = useMemo(() => {
    const edges = visibleEdges.map(e => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type: "azure",
      selected: selectedEdgeId ? e.id === selectedEdgeId : false,
      data: {
        label: e.relationship,
        origin: e.origin,
        status: e.status,
        confidence: e.confidence,
        user_customized:
          userLayerEnabled &&
          (e.origin === "manual" || e.status === "accepted" || e.status === "rejected"),
      },
      markerEnd: { type: MarkerType.ArrowClosed },
    }));
    
    // Debug logging for manual edges
    const manualEdges = edges.filter(e => e.data.origin === "manual");
    if (manualEdges.length > 0) {
      console.log("Manual edges found:", manualEdges);
    }
    
    return edges;
  }, [visibleEdges, userLayerEnabled, selectedEdgeId]);

  // Layout using Dagre
  const layoutedNodes = useMemo(() => {
    if (rfNodes.length === 0) return rfNodes;

    const g = new dagre.graphlib.Graph();
    g.setGraph({
      rankdir: "TB",
      nodesep: 100,
      ranksep: 180,
      marginx: 40,
      marginy: 40,
    });
    g.setDefaultEdgeLabel(() => ({}));

    rfNodes.forEach(node => {
      g.setNode(node.id, { width: NODE_W, height: NODE_H });
    });

    rfEdges.forEach(edge => {
      g.setEdge(edge.source, edge.target);
    });

    dagre.layout(g);

    return rfNodes.map(node => {
      const pos = g.node(node.id);
      if (!pos) return { ...node, position: { x: 0, y: 0 } };
      return {
        ...node,
        position: { x: pos.x - NODE_W / 2, y: pos.y - NODE_H / 2 },
      };
    });
  }, [rfNodes, rfEdges]);

  // Hydrate group containers based on node metadata.
  useEffect(() => {
    const membersByGroup = new Map<string, { label: string | null; memberIds: string[] }>();
    for (const n of visibleNodes) {
      const info = groupInfoByNodeId.get(n.id);
      if (!info) continue;
      if (!membersByGroup.has(info.groupId)) {
        membersByGroup.set(info.groupId, { label: info.label, memberIds: [] });
      }
      const entry = membersByGroup.get(info.groupId)!;
      entry.memberIds.push(n.id);
      if (!entry.label && info.label) entry.label = info.label;
    }

    const absPosById = new Map(layoutedNodes.map(n => [n.id, n.position] as const));

    setGroups(prev => {
      const prevById = new Map(prev.map(g => [g.id, g] as const));
      const next: GroupState[] = [];

      for (const [gid, entry] of membersByGroup.entries()) {
        if (entry.memberIds.length < 2) continue;

        const membersWithPos = entry.memberIds
          .map(id => ({ id, pos: absPosById.get(id) }))
          .filter((x): x is { id: string; pos: { x: number; y: number } } => !!x.pos);

        if (membersWithPos.length < 2) continue;

        let minX = Number.POSITIVE_INFINITY;
        let minY = Number.POSITIVE_INFINITY;
        let maxX = Number.NEGATIVE_INFINITY;
        let maxY = Number.NEGATIVE_INFINITY;

        for (const { pos } of membersWithPos) {
          minX = Math.min(minX, pos.x);
          minY = Math.min(minY, pos.y);
          maxX = Math.max(maxX, pos.x + NODE_W);
          maxY = Math.max(maxY, pos.y + NODE_H);
        }

        const rect: GroupRect = {
          x: minX - GROUP_PAD,
          y: minY - GROUP_PAD,
          width: maxX - minX + GROUP_PAD * 2,
          height: maxY - minY + GROUP_PAD * 2,
        };

        const childPositions: Record<string, { x: number; y: number }> = {};
        for (const { id, pos } of membersWithPos) {
          childPositions[id] = { x: pos.x - rect.x, y: pos.y - rect.y };
        }

        const prevGroup = prevById.get(gid);
        next.push({
          id: gid,
          label: entry.label ?? gid,
          memberIds: [...entry.memberIds],
          collapsed: prevGroup?.collapsed ?? false,
          rect,
          childPositions,
        });
      }

      // Keep locally-created groups that haven't been persisted yet.
      for (const g of prev) {
        if (membersByGroup.has(g.id)) continue;
        if (g.id.startsWith("local-")) next.push(g);
      }

      return next;
    });
  }, [visibleNodes, groupInfoByNodeId, layoutedNodes]);

  const groupMembership = useMemo(() => {
    const map = new Map<string, string>();
    for (const g of groups) {
      for (const id of g.memberIds) map.set(id, g.id);
    }
    return map;
  }, [groups]);

  const toggleGroupCollapsed = useCallback((groupId: string) => {
    setGroups(prev => prev.map(g => (g.id === groupId ? { ...g, collapsed: !g.collapsed } : g)));
  }, []);

  const composedNodes = useMemo((): Node[] => {
    const byId = new Map(layoutedNodes.map(n => [n.id, n] as const));

    const groupNodes: Node[] = groups.map(g => {
      const w = g.collapsed ? COLLAPSED_GROUP_W : g.rect.width;
      const h = g.collapsed ? COLLAPSED_GROUP_H : g.rect.height;
      return {
        id: g.id,
        type: "azureGroup",
        position: { x: g.rect.x, y: g.rect.y },
        data: {
          label: g.label,
          count: g.memberIds.length,
          collapsed: g.collapsed,
          onToggleCollapsed: () => toggleGroupCollapsed(g.id),
        },
        selectable: true,
        draggable: true,
        connectable: false,
        zIndex: 0,
        style: { width: w, height: h },
      } as Node;
    });

    const baseNodes: Node[] = layoutedNodes.map(n => {
      const groupId = groupMembership.get(n.id);
      if (!groupId) {
        // For non-grouped nodes, check if position overlaps with any group
        // If it does, move it outside the group bounds
        let adjustedPos = n.position;
        for (const g of groups) {
          const nodeLeft = n.position.x;
          const nodeTop = n.position.y;
          const nodeRight = nodeLeft + NODE_W;
          const nodeBottom = nodeTop + NODE_H;
          
          const groupLeft = g.rect.x;
          const groupTop = g.rect.y;
          const groupRight = g.rect.x + g.rect.width;
          const groupBottom = g.rect.y + g.rect.height;
          
          // Check if node overlaps with group
          if (nodeLeft < groupRight && nodeRight > groupLeft && 
              nodeTop < groupBottom && nodeBottom > groupTop) {
            // Move node to the right of the group
            adjustedPos = { x: groupRight + 20, y: n.position.y };
            break; // Only adjust once
          }
        }
        return { ...n, position: adjustedPos, zIndex: 10 };
      }

      const g = groups.find(x => x.id === groupId);
      if (!g) return { ...n, zIndex: 10 };

      const rel = g.childPositions[n.id] ?? {
        x: n.position.x - g.rect.x,
        y: n.position.y - g.rect.y,
      };

      return {
        ...n,
        parentNode: g.id,
        position: rel,
        hidden: g.collapsed,
        zIndex: 1,
        data: {
          ...(typeof n.data === 'object' ? n.data : {}),
          groupId: groupId,
          onRemoveFromGroup: () => onNodeRemoveFromGroup?.({ nodeId: n.id, groupId }),
        },
      };
    });

    const survivingGroups = groupNodes.filter(gn => {
      const state = groups.find(s => s.id === gn.id);
      if (!state) return false;
      return state.memberIds.some(id => byId.has(id));
    });

    return [...survivingGroups, ...baseNodes];
  }, [layoutedNodes, groups, groupMembership, toggleGroupCollapsed]);

  const composedEdges = useMemo((): Edge[] => {
    if (groups.length === 0) return rfEdges;

    const collapsedGroupByMember = new Map<string, string>();
    const collapsedGroupIds = new Set<string>();
    for (const g of groups) {
      if (!g.collapsed) continue;
      collapsedGroupIds.add(g.id);
      for (const id of g.memberIds) collapsedGroupByMember.set(id, g.id);
    }
    if (collapsedGroupIds.size === 0) return rfEdges;

    const agg = new Map<string, { base: Edge; count: number }>();
    const mapEndpoint = (nodeId: string): string => collapsedGroupByMember.get(nodeId) ?? nodeId;

    for (const e of rfEdges) {
      const src = mapEndpoint(e.source);
      const tgt = mapEndpoint(e.target);

      // Hide internal edges inside a collapsed group.
      if (src === tgt && collapsedGroupIds.has(src)) continue;

      const data: any = (e.data as any) || {};
      const relationship = typeof data.label === "string" ? data.label : "";
      const origin = data.origin ?? "";
      const status = data.status ?? "";
      const key = `${src}|${relationship}|${tgt}|${origin}|${status}`;

      if (!agg.has(key)) {
        const cloned: Edge = {
          ...e,
          id: `agg-${key}`,
          source: src,
          target: tgt,
          data: { ...data },
        };
        agg.set(key, { base: cloned, count: 1 });
      } else {
        agg.get(key)!.count += 1;
      }
    }

    return Array.from(agg.values()).map(({ base, count }) => {
      const data: any = (base.data as any) || {};
      const label = typeof data.label === "string" ? data.label : "";
      return {
        ...base,
        data: {
          ...data,
          label: count > 1 ? `${label} ×${count}` : label,
        },
      };
    });
  }, [rfEdges, groups]);

  const [flowNodes, setFlowNodes, onNodesChange] = useNodesState(composedNodes);
  const [flowEdges, setFlowEdges, onEdgesChange] = useEdgesState(composedEdges);

  useEffect(() => {
    setFlowNodes(composedNodes);
  }, [composedNodes, setFlowNodes]);

  useEffect(() => {
    setFlowEdges(composedEdges);
  }, [composedEdges, setFlowEdges]);

  useEffect(() => {
    setTimeout(() => fitView(), 100);
  }, [fitView]);

  const handleNodeClick = useCallback(
    (event: React.MouseEvent, node: Node) => {
      event.stopPropagation();
      if (node.type === "azureGroup") return;
      const graphNode = visibleNodes.find(n => n.id === node.id);
      if (graphNode) onNodeSelected?.(node.id);
    },
    [visibleNodes, onNodeSelected]
  );

  const handleSelectionChange = useCallback((params: OnSelectionChangeParams) => {
    const ids = (params.nodes ?? []).filter(n => n.type !== "azureGroup").map(n => n.id);
    setSelectedNodeIds(ids);

    const selectedGroup = (params.nodes ?? []).find(n => n.type === "azureGroup");
    setSelectedGroupId(selectedGroup ? selectedGroup.id : null);
  }, []);

  const createGroupFromSelection = useCallback(
    async (label: string) => {
      if (selectedNodeIds.length < 2) return;
      if (groupBusy) return;

      const trimmedLabel = label.trim();
      if (!trimmedLabel) return;

      const selectedSet = new Set(selectedNodeIds);

      const byId = new Map(flowNodes.map(n => [n.id, n] as const));
      const getAbsPos = (id: string): { x: number; y: number } | null => {
        const n = byId.get(id);
        if (!n) return null;
        if (!n.parentNode) return n.position;
        const parent = byId.get(n.parentNode);
        if (!parent) return n.position;
        return { x: parent.position.x + n.position.x, y: parent.position.y + n.position.y };
      };

      const members = [...selectedSet].filter(id => visibleNodeIds.has(id));
      if (members.length < 2) return;

      const abs = members
        .map(id => ({ id, pos: getAbsPos(id) }))
        .filter((x): x is { id: string; pos: { x: number; y: number } } => !!x.pos);
      if (abs.length < 2) return;

      let minX = Number.POSITIVE_INFINITY;
      let minY = Number.POSITIVE_INFINITY;
      let maxX = Number.NEGATIVE_INFINITY;
      let maxY = Number.NEGATIVE_INFINITY;

      for (const { pos } of abs) {
        minX = Math.min(minX, pos.x);
        minY = Math.min(minY, pos.y);
        maxX = Math.max(maxX, pos.x + NODE_W);
        maxY = Math.max(maxY, pos.y + NODE_H);
      }

      const rect: GroupRect = {
        x: minX - GROUP_PAD,
        y: minY - GROUP_PAD,
        width: maxX - minX + GROUP_PAD * 2,
        height: maxY - minY + GROUP_PAD * 2,
      };

      const childPositions: Record<string, { x: number; y: number }> = {};
      for (const { id, pos } of abs) {
        childPositions[id] = { x: pos.x - rect.x, y: pos.y - rect.y };
      }

      const newId = `grp-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      const localId = `local-${newId}`;
      const groupLabel = trimmedLabel;
      const newGroup: GroupState = {
        id: localId,
        label: groupLabel,
        memberIds: abs.map(x => x.id),
        collapsed: false,
        rect,
        childPositions,
      };

      setGroups(prev => {
        const cleaned = prev
          .map(g => ({ ...g, memberIds: g.memberIds.filter(id => !selectedSet.has(id)) }))
          .filter(g => g.memberIds.length >= 2);
        return [...cleaned, newGroup];
      });

      setGroupBusy(true);
      try {
        await onGroupCreate?.({ groupId: newId, label: groupLabel, memberIds: newGroup.memberIds });
        setGroups(prev => prev.map(g => (g.id === localId ? { ...g, id: newId } : g)));
      } catch (e) {
        console.error("Failed to persist group", e);
        setGroups(prev => prev.filter(g => g.id !== localId));
      } finally {
        setGroupBusy(false);
      }
    },
    [selectedNodeIds, groupBusy, flowNodes, visibleNodeIds, onGroupCreate]
  );

  useEffect(() => {
    if (!groupCreateRequest) return;
    if (lastCreateNonceRef.current === groupCreateRequest.nonce) return;
    lastCreateNonceRef.current = groupCreateRequest.nonce;
    createGroupFromSelection(groupCreateRequest.label);
  }, [groupCreateRequest, createGroupFromSelection]);

  const selectedGroupState = useMemo(() => {
    if (!selectedGroupId) return null;
    return groups.find(g => g.id === selectedGroupId) ?? null;
  }, [selectedGroupId, groups]);

  useEffect(() => {
    onSelectionStateChange?.({
      selectedNodeIds,
      selectedGroupId,
      selectedGroupLabel: selectedGroupState?.label,
      selectedGroupMemberIds: selectedGroupState?.memberIds,
    });
  }, [onSelectionStateChange, selectedNodeIds, selectedGroupId, selectedGroupState]);

  const handleNodeDragStop = useCallback<NodeDragHandler>(
    async (_event, node) => {
      if (node.type === "azureGroup") return;
      if (groupBusy) return;

      const byId = new Map(flowNodes.map(n => [n.id, n] as const));
      const n = byId.get(node.id);
      if (!n) return;

      const abs = (() => {
        if (!n.parentNode) return n.position;
        const parent = byId.get(n.parentNode);
        if (!parent) return n.position;
        return { x: parent.position.x + n.position.x, y: parent.position.y + n.position.y };
      })();

      const cx = abs.x + NODE_W / 2;
      const cy = abs.y + NODE_H / 2;

      const parseDim = (v: unknown, fallback: number): number => {
        if (typeof v === "number") return v;
        if (typeof v === "string") {
          const n = Number(v);
          return Number.isFinite(n) ? n : fallback;
        }
        return fallback;
      };

      const groupNodes = flowNodes.filter(x => x.type === "azureGroup");
      const hit = groupNodes.find(g => {
        const w = parseDim((g as any).width ?? (g.style as any)?.width, COLLAPSED_GROUP_W);
        const h = parseDim((g as any).height ?? (g.style as any)?.height, COLLAPSED_GROUP_H);
        const left = g.position.x;
        const top = g.position.y;
        return cx >= left && cx <= left + w && cy >= top && cy <= top + h;
      });

      if (!hit) return;
      const targetGroupId = hit.id;
      const currentGroupId = groupMembership.get(node.id);
      if (currentGroupId === targetGroupId) return;

      const targetLabel = groups.find(g => g.id === targetGroupId)?.label ?? null;

      setGroups(prev => {
        // Absolute positions for currently rendered nodes (account for parent groups).
        const absById = new Map<string, { x: number; y: number }>();
        for (const fn of flowNodes) {
          if (!fn.parentNode) {
            absById.set(fn.id, fn.position);
          } else {
            const parent = flowNodes.find(x => x.id === fn.parentNode);
            if (parent) {
              absById.set(fn.id, {
                x: parent.position.x + fn.position.x,
                y: parent.position.y + fn.position.y,
              });
            }
          }
        }

        const next = prev.map(g => ({
          ...g,
          memberIds: [...g.memberIds],
          childPositions: { ...g.childPositions },
        }));

        for (const g of next) {
          g.memberIds = g.memberIds.filter(id => id !== node.id);
          delete g.childPositions[node.id];
        }

        const target = next.find(g => g.id === targetGroupId);
        if (!target) return prev;

        if (!target.memberIds.includes(node.id)) target.memberIds.push(node.id);

        // Recompute rect based on absolute positions of members.
        const membersWithAbs = target.memberIds
          .map(id => ({ id, pos: absById.get(id) }))
          .filter((x): x is { id: string; pos: { x: number; y: number } } => !!x.pos);

        if (membersWithAbs.length >= 2) {
          let minX = Number.POSITIVE_INFINITY;
          let minY = Number.POSITIVE_INFINITY;
          let maxX = Number.NEGATIVE_INFINITY;
          let maxY = Number.NEGATIVE_INFINITY;

          for (const { pos } of membersWithAbs) {
            minX = Math.min(minX, pos.x);
            minY = Math.min(minY, pos.y);
            maxX = Math.max(maxX, pos.x + NODE_W);
            maxY = Math.max(maxY, pos.y + NODE_H);
          }

          target.rect = {
            x: minX - GROUP_PAD,
            y: minY - GROUP_PAD,
            width: maxX - minX + GROUP_PAD * 2,
            height: maxY - minY + GROUP_PAD * 2,
          };

          const updatedChildPositions: Record<string, { x: number; y: number }> = {};
          for (const { id, pos } of membersWithAbs) {
            updatedChildPositions[id] = {
              x: pos.x - target.rect.x,
              y: pos.y - target.rect.y,
            };
          }
          target.childPositions = updatedChildPositions;
        } else {
          // Fallback for single-member group: keep relative position from the drop hitbox.
          target.childPositions[node.id] = {
            x: abs.x - hit.position.x,
            y: abs.y - hit.position.y,
          };
        }

        return next;
      });

      setPendingGroupByNodeId(prev => ({
        ...prev,
        [node.id]: { groupId: targetGroupId, label: targetLabel },
      }));

      try {
        setGroupBusy(true);
        await onMoveNodeToGroup?.({ nodeId: node.id, groupId: targetGroupId });
      } catch (e) {
        console.error("Failed to move node into group", e);
      } finally {
        setGroupBusy(false);
      }
    },
    [flowNodes, groupMembership, groupBusy, onMoveNodeToGroup, groups]
  );

  const handleEdgeClick = useCallback(
    (event: React.MouseEvent, edge: Edge) => {
      event.stopPropagation();
      const graphEdge = visibleEdges.find(e => e.id === edge.id);
      if (graphEdge) onEdgeSelected?.(graphEdge);
      else onEdgeSelected?.(null);
    },
    [visibleEdges, onEdgeSelected]
  );

  const handlePaneClick = useCallback(() => {
    onNodeSelected?.(null);
    onEdgeSelected?.(null);
  }, [onNodeSelected, onEdgeSelected]);

  const handleConnect = useCallback(
    (connection: Connection) => {
      if (connection.source && connection.target) {
        // Swap source and target because React Flow's connection model may be reversed
        // When user drags from node A to node B, we want A -> B (A depends on B)
        onEdgeCreate?.(connection.target, connection.source);
      }
    },
    [onEdgeCreate]
  );

  return (
    <ReactFlow
      nodes={flowNodes}
      edges={flowEdges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={handleNodeClick}
      onNodeDragStop={handleNodeDragStop}
      onNodeDoubleClick={(event, node) => {
        if (node.type === "azureGroup") return;
        event.stopPropagation();
        onNodeRename?.(node.id);
      }}
      onEdgeClick={handleEdgeClick}
      onPaneClick={handlePaneClick}
      onConnect={handleConnect}
      onSelectionChange={handleSelectionChange}
      nodeTypes={nodeTypesWithGroups}
      edgeTypes={edgeTypes}
      connectionMode={ConnectionMode.Loose}
      defaultEdgeOptions={{ zIndex: 10 }}
      style={{ width: "100%", height: "100%" }}
    >
      <Controls />
    </ReactFlow>
  );
});

GraphCanvas.displayName = 'GraphCanvas';

export default GraphCanvas;
