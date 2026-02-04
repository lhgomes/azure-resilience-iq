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
  applyNodeChanges,
  NodeChange,
} from "reactflow";
import "reactflow/dist/style.css";
import dagre from "dagre";
import AzureNode from "./AzureNode";
import AzureEdge from "./AzureEdge";
import AzureGroupNode from "./AzureGroupNode";
import { getEdgeHandles } from "../utils/edgeHandles";

export interface GraphNode {
  id: string;
  name: string;
  type: string;
  element_weight?: number;
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
  created_by?: string;
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
const GROUP_GAP_X = 100;
const GROUP_GAP_Y = 180;
const MAX_GROUP_SPREAD = (NODE_W + GROUP_GAP_X) * 3;
const GRID_X = NODE_W + GROUP_GAP_X;
const GRID_Y = NODE_H + GROUP_GAP_Y;
const COLLAPSED_GROUP_W = 220;
const COLLAPSED_GROUP_H = 72;

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
  groups?: Array<{ id: string; name: string; nodes: string[] }>;
  userLayerEnabled: boolean;
  aiLayerEnabled: boolean;
  onAiLayerEnabledChange?: (enabled: boolean) => void;
  onUserLayerEnabledChange?: (enabled: boolean) => void;
  graphViewState?: {
    viewport?: { x: number; y: number; zoom: number };
    node_positions?: Record<string, { x: number; y: number }>;
  } | null;
  onGraphViewApplied?: () => void;
  selectedEdgeId?: string | null;
  onEdgeSelected?: (edge: GraphEdge | null) => void;
  onNodeSelected?: (nodeId: string | null) => void;
  maxImportance?: number;
  onEdgeCreate?: (sourceId: string, targetId: string) => void;
  onNodeRename?: (nodeId: string) => void;
  onNodeHide?: (nodeId: string) => void;
  onNodeDragStart?: () => void;

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
  resetLayout: () => void;
  getViewState: () => {
    viewport: { x: number; y: number; zoom: number };
    node_positions: Record<string, { x: number; y: number }>;
  } | null;
  setViewState: (state: {
    viewport?: { x: number; y: number; zoom: number };
    node_positions?: Record<string, { x: number; y: number }>;
  } | null) => void;
}

const GraphCanvas = forwardRef<GraphCanvasHandle, Props>((props, ref) => {
  const {
    nodes: nodesProp,
    edges: edgesProp,
    groups: groupsProp = [],
    userLayerEnabled,
    graphViewState,
    onGraphViewApplied,
    onEdgeSelected,
    onNodeSelected,
    maxImportance = 1,
    onEdgeCreate,
    onNodeRename,
      onNodeHide,
    onNodeDragStart,
    onGroupCreate,
    groupCreateRequest,
    onMoveNodeToGroup,
    onRemoveNodeFromGroup,
    onNodeRemoveFromGroup,
    onSelectionStateChange,
    selectedEdgeId = null,
  } = props;
  
  const { fitView, getViewport, setViewport } = useReactFlow();
  const flowNodesRef = useRef<Node[]>([]);
  const [savedPositions, setSavedPositions] = useState<Record<string, { x: number; y: number }>>({});
  const skipNextFitViewRef = useRef(false);
  const lastMissingLogRef = useRef<string | null>(null);
  const justAppliedGraphViewRef = useRef(false);
  const layoutResetInFlightRef = useRef(false);

  const normalizePositions = useCallback((positions?: Record<string, { x: number; y: number }>) => {
    if (!positions) return {} as Record<string, { x: number; y: number }>;
    return Object.fromEntries(
      Object.entries(positions).map(([id, pos]) => [String(id).toLowerCase(), { x: pos.x, y: pos.y }])
    );
  }, []);

  const [groups, setGroups] = useState<GroupState[]>([]);
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const manualGroupSelectionRef = useRef<string | null>(null);
  const [groupBusy, setGroupBusy] = useState(false);
  const lastCreateNonceRef = useRef<number | null>(null);
  const [pendingGroupByNodeId, setPendingGroupByNodeId] = useState<
    Record<string, { groupId: string; label?: string | null }>
  >({});

  // Optimistically remove a node from a group in local state
  const removeMemberFromLocalGroups = useCallback((nodeId: string, groupId: string) => {
    const nodeKey = String(nodeId).toLowerCase();
    const groupKey = String(groupId).toLowerCase();
    setGroups(prev =>
      prev
        .map(g => {
          if (String(g.id).toLowerCase() !== groupKey) return g;
          const nextMembers = g.memberIds.filter(id => String(id).toLowerCase() !== nodeKey);
          return { ...g, memberIds: nextMembers };
        })
        .filter(g => g.memberIds.length >= 2)
    );
  }, []);

  // Filter nodes by importance
  const visibleNodes = useMemo(() => {
    return nodesProp.filter(n => {
      const meta = n.metadata ?? {};
      const importance = typeof meta["importance"] === "number" ? (meta["importance"] as number) : 3;
      return importance <= maxImportance;
    });
  }, [nodesProp, maxImportance]);

  const visibleNodeIds = useMemo(() => new Set(visibleNodes.map(n => n.id)), [visibleNodes]);

  // When the node set changes (e.g., new graph loaded), clear saved positions so they get fresh Dagre layout.
  // But DON'T clear if graphViewState is pending (it will re-apply positions) or if we just applied positions.
  useEffect(() => {
    if (graphViewState?.node_positions) {
      // graphViewState is pending, will apply positions - don't clear yet
      return;
    }
    
    if (justAppliedGraphViewRef.current) {
      // We just applied positions from graphViewState, don't clear them now
      justAppliedGraphViewRef.current = false;
      return;
    }

    const currentIds = new Set(visibleNodes.map(n => String(n.id).toLowerCase()));
    const savedIds = new Set(Object.keys(savedPositions));
    
    // Check if node set has substantially changed (more than just filtering)
    const oldHasSaved = savedIds.size > 0;
    const currentHasNodes = currentIds.size > 0;
    const noOverlap = Array.from(currentIds).every(id => !savedIds.has(id));
    
    if (oldHasSaved && currentHasNodes && noOverlap) {
      setSavedPositions({});
    }
  }, [visibleNodes, savedPositions, graphViewState?.node_positions]);

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
          element_weight: typeof n.element_weight === "number" ? n.element_weight : undefined,
          color: typeof meta["color"] === "string" ? (meta["color"] as string) : undefined,
          azure_service_category: typeof meta["azure_service_category"] === "string" ? (meta["azure_service_category"] as string) : undefined,
          azure_service_name: typeof meta["azure_service_name"] === "string" ? (meta["azure_service_name"] as string) : undefined,
          ai_annotation: !!meta["ai_annotation"],
          user_customized: isUserCustomized,
          ai_tooltip: meta["ai_tooltip"],
          user_tooltip: meta["user_tooltip"],
          metadata: meta,
          onRemoveFromGraph: onNodeHide ? () => onNodeHide(n.id) : undefined,
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

    const snap = (value: number, grid: number): number => Math.round(value / grid) * grid;

    return rfNodes.map(node => {
      const pos = g.node(node.id);
      const saved = savedPositions[String(node.id).toLowerCase()];
      if (saved) {
        return {
          ...node,
          position: { x: saved.x, y: saved.y },
        };
      }
      if (!pos) return { ...node, position: { x: 0, y: 0 } };
      return {
        ...node,
        position: {
          x: snap(pos.x - NODE_W / 2, GRID_X),
          y: snap(pos.y - NODE_H / 2, GRID_Y),
        },
      };
    });
  }, [rfNodes, rfEdges, savedPositions]);

  // Keep group members compact on refresh by seeding missing saved positions
  useEffect(() => {
    if (groups.length === 0 || layoutedNodes.length === 0) return;

    const byId = new Map(layoutedNodes.map(n => [String(n.id).toLowerCase(), n] as const));

    setSavedPositions(prev => {
      let changed = false;
      const next = { ...prev };

      for (const group of groups) {
        const memberIds = group.memberIds;
        if (memberIds.length === 0) continue;

        const positions = memberIds
          .map(id => ({ id, node: byId.get(String(id).toLowerCase()) }))
          .filter((x): x is { id: string; node: Node } => !!x.node)
          .map(x => ({ id: x.id, pos: x.node.position }));

        if (positions.length === 0) continue;

        let minX = Number.POSITIVE_INFINITY;
        let minY = Number.POSITIVE_INFINITY;
        for (const { pos } of positions) {
          minX = Math.min(minX, pos.x);
          minY = Math.min(minY, pos.y);
        }

        const SPACING_X = NODE_W + GROUP_GAP_X;
        const SPACING_Y = NODE_H + GROUP_GAP_Y;
        const GRID_COLS = 3;

        memberIds.forEach((memberId, index) => {
          const key = String(memberId).toLowerCase();
          if (next[key]) return;
          const row = Math.floor(index / GRID_COLS);
          const col = index % GRID_COLS;
          next[key] = {
            x: minX + col * SPACING_X,
            y: minY + row * SPACING_Y,
          };
          changed = true;
        });
      }

      return changed ? next : prev;
    });
  }, [groups, layoutedNodes]);

  // Add handle selection based on laid out node positions
  const edgesWithHandles: Edge[] = useMemo(() => {
    return rfEdges.map(e => {
      // Find source and target node positions from laid out nodes
      const sourceNode = layoutedNodes.find(n => n.id === e.source);
      const targetNode = layoutedNodes.find(n => n.id === e.target);
      
      if (!sourceNode || !targetNode) {
        return { ...e, sourceHandle: "bottom", targetHandle: "top" };
      }

      const { sourceHandle, targetHandle } = getEdgeHandles(
        sourceNode.position,
        targetNode.position
      );

      return { ...e, sourceHandle, targetHandle };
    });
  }, [rfEdges, layoutedNodes]);

  // Memoize a stable key for group changes
  const groupsKey = useMemo(() => {
    const key = groupsProp.map(g => `${g.id}:${g.nodes.join(',')}`).join('|');
    return key;
  }, [groupsProp]);

  // Load groups from prop
  useEffect(() => {
    if (groupsProp.length === 0) {
      setGroups(prev => {
        const next = prev.filter(g => g.id.startsWith("local-"));
        return next.length === prev.length ? prev : next;
      });
      return;
    }

    setGroups(prev => {
      const newGroups = groupsProp
        .filter(g => g.nodes.length >= 2)
        .map(g => {
          const prevGroup = prev.find(pg => pg.id === g.id);
          return {
            id: g.id,
            label: g.name,
            memberIds: [...g.nodes], // Clone array to ensure new reference
            collapsed: prevGroup?.collapsed ?? false,
            rect: { x: 0, y: 0, width: 300, height: 300 },
            childPositions: {},
          };
        });

      const localGroups = prev.filter(pg => pg.id.startsWith("local-"));
      return [...newGroups, ...localGroups];
    });
  }, [groupsKey, groupsProp]);

  const groupMembership = useMemo(() => {
    const map = new Map<string, string>();
    for (const g of groups) {
      for (const id of g.memberIds) {
        map.set(id, g.id);
      }
    }
    return map;
  }, [groups]);

  const toggleGroupCollapsed = useCallback((groupId: string) => {
    setGroups(prev => prev.map(g => (g.id === groupId ? { ...g, collapsed: !g.collapsed } : g)));
  }, []);

  const composedNodes = useMemo((): Node[] => {
    const byId = new Map(layoutedNodes.map(n => [n.id, n] as const));

    const memberOrderForGroup = (group: GroupState): string[] => {
      const memberSet = new Set(group.memberIds);
      const scoreById = new Map<string, number>();

      for (const edge of rfEdges) {
        const srcIn = memberSet.has(edge.source);
        const tgtIn = memberSet.has(edge.target);
        if (srcIn === tgtIn) continue;

        const externalId = srcIn ? edge.target : edge.source;
        const memberId = srcIn ? edge.source : edge.target;
        const externalPos = byId.get(externalId)?.position;
        if (!externalPos) continue;

        const prev = scoreById.get(memberId);
        scoreById.set(memberId, prev === undefined ? externalPos.x : (prev + externalPos.x) / 2);
      }

      return [...group.memberIds].sort((a, b) => {
        const aScore = scoreById.get(a);
        const bScore = scoreById.get(b);
        if (aScore === undefined && bScore === undefined) return 0;
        if (aScore === undefined) return 1;
        if (bScore === undefined) return -1;
        return aScore - bScore;
      });
    };

    // Calculate proper bounds for each group from layoutedNodes positions
    const groupBounds = new Map<string, { rect: GroupRect; childPositions: Record<string, { x: number; y: number }> }>();
    for (const g of groups) {
      const orderedMembers = memberOrderForGroup(g);
      const membersWithPos = orderedMembers
        .map(id => ({ id, pos: byId.get(id)?.position }))
        .filter((x): x is { id: string; pos: { x: number; y: number } } => !!x.pos);

      if (membersWithPos.length > 0) {
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

        const spreadW = maxX - minX + NODE_W;
        const spreadH = maxY - minY + NODE_H;

        let rect = {
          x: minX - GROUP_PAD,
          y: minY - GROUP_PAD,
          width: maxX - minX + GROUP_PAD * 2,
          height: maxY - minY + GROUP_PAD * 2,
        };

        const childPositions: Record<string, { x: number; y: number }> = {};

        if (spreadW > MAX_GROUP_SPREAD || spreadH > MAX_GROUP_SPREAD) {
          const count = orderedMembers.length;
          const cols = Math.min(count, 3);
          const rows = Math.ceil(count / 3);
          const gridWidth = GROUP_PAD * 2 + cols * NODE_W + Math.max(0, cols - 1) * GROUP_GAP_X;
          const gridHeight = GROUP_PAD * 2 + rows * NODE_H + Math.max(0, rows - 1) * GROUP_GAP_Y;

          rect = {
            x: minX - GROUP_PAD,
            y: minY - GROUP_PAD,
            width: gridWidth,
            height: gridHeight,
          };

          orderedMembers.forEach((memberId, index) => {
            const col = index % 3;
            const row = Math.floor(index / 3);
            childPositions[memberId] = {
              x: GROUP_PAD + col * (NODE_W + GROUP_GAP_X),
              y: GROUP_PAD + row * (NODE_H + GROUP_GAP_Y),
            };
          });
        } else {
          // Add childPositions for all members, whether or not they're in layoutedNodes
          for (const memberId of orderedMembers) {
            const nodeInLayout = byId.get(memberId);
            if (nodeInLayout) {
              childPositions[memberId] = { x: nodeInLayout.position.x - rect.x, y: nodeInLayout.position.y - rect.y };
            } else {
              const index = orderedMembers.indexOf(memberId);
              const col = index % 3;
              const row = Math.floor(index / 3);
              childPositions[memberId] = {
                x: GROUP_PAD + col * (NODE_W + GROUP_GAP_X),
                y: GROUP_PAD + row * (NODE_H + GROUP_GAP_Y),
              };
            }
          }
        }

        groupBounds.set(g.id, { rect, childPositions });
      }
    }

    const groupNodes: Node[] = groups
      .filter(g => groupBounds.has(g.id)) // Only include groups with visible members
      .map(g => {
      const bounds = groupBounds.get(g.id)!;
      const rect = bounds.rect;
      const w = g.collapsed ? COLLAPSED_GROUP_W : rect.width;
      const h = g.collapsed ? COLLAPSED_GROUP_H : rect.height;
      return {
        id: g.id,
        type: "azureGroup",
        position: { x: rect.x, y: rect.y },
        data: {
          label: g.label,
          count: g.memberIds.length,
          collapsed: g.collapsed,
          onToggleCollapsed: () => toggleGroupCollapsed(g.id),
          onSelect: () => {
            setSelectedNodeIds([]);
            setSelectedGroupId(g.id);
            manualGroupSelectionRef.current = g.id;
          },
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
          const bounds = groupBounds.get(g.id);
          if (!bounds) continue;
          const { rect } = bounds;
          
          const nodeLeft = n.position.x;
          const nodeTop = n.position.y;
          const nodeRight = nodeLeft + NODE_W;
          const nodeBottom = nodeTop + NODE_H;
          
          const groupLeft = rect.x;
          const groupTop = rect.y;
          const groupRight = rect.x + rect.width;
          const groupBottom = rect.y + rect.height;
          
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

      const bounds = groupBounds.get(g.id);
      const rect = bounds?.rect ?? g.rect;
      const childPositions = bounds?.childPositions ?? {};

      const rel = childPositions[n.id] ?? {
        x: n.position.x - rect.x,
        y: n.position.y - rect.y,
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
          onRemoveFromGroup: () => {
            removeMemberFromLocalGroups(n.id, groupId);
            onNodeRemoveFromGroup?.({ nodeId: n.id, groupId });
          },
        },
      };
    });

    const survivingGroups = groupNodes.filter(gn => {
      const state = groups.find(s => s.id === gn.id);
      if (!state) return false;
      // Always show groups that exist in state - they have already been validated
      // by groupsFromProp calculation which checks member positions
      return true;
    });

    return [...survivingGroups, ...baseNodes];
  }, [layoutedNodes, groups, rfEdges, groupMembership, toggleGroupCollapsed, removeMemberFromLocalGroups]);

  const composedEdges = useMemo((): Edge[] => {
    // Filter out edges targeting group IDs (groups are visual containers, not graph nodes)
    const groupIdSet = new Set(groups.map(g => g.id.toLowerCase()));
    const validEdges = edgesWithHandles.filter(e => {
      const srcLower = e.source.toLowerCase();
      const tgtLower = e.target.toLowerCase();
      return !groupIdSet.has(srcLower) && !groupIdSet.has(tgtLower);
    });

    if (groups.length === 0) return validEdges;

    const collapsedGroupByMember = new Map<string, string>();
    const collapsedGroupIds = new Set<string>();
    for (const g of groups) {
      if (!g.collapsed) continue;
      collapsedGroupIds.add(g.id);
      for (const id of g.memberIds) collapsedGroupByMember.set(id, g.id);
    }
    if (collapsedGroupIds.size === 0) return validEdges;

    const agg = new Map<string, { base: Edge; count: number }>();
    const mapEndpoint = (nodeId: string): string => collapsedGroupByMember.get(nodeId) ?? nodeId;

    for (const e of validEdges) {
      const src = mapEndpoint(e.source);
      const tgt = mapEndpoint(e.target);

      // If an endpoint is remapped to a group, clear handles to let React Flow default them
      const sourceHandle = src !== e.source ? undefined : e.sourceHandle;
      const targetHandle = tgt !== e.target ? undefined : e.targetHandle;

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
          sourceHandle,
          targetHandle,
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
  }, [edgesWithHandles, groups]);

  // Ensure we never pass an edge whose endpoints are missing from the composed node set.
  const composedNodeIds = useMemo(() => new Set(composedNodes.map(n => n.id)), [composedNodes]);
  const safeEdges = useMemo(
    () => composedEdges.filter(e => composedNodeIds.has(e.source) && composedNodeIds.has(e.target)),
    [composedEdges, composedNodeIds]
  );

  const [flowNodes, setFlowNodes, onNodesChangeDefault] = useNodesState(composedNodes);
  const [flowEdges, setFlowEdges, onEdgesChange] = useEdgesState(safeEdges);

  // Expose fitView to parent via ref
  useImperativeHandle(ref, () => ({
    fitView: () => {
      setTimeout(() => fitView(), 500);
    },
    resetLayout: () => {
      if (layoutResetInFlightRef.current) return;
      layoutResetInFlightRef.current = true;
      setSavedPositions({});
      setTimeout(() => {
        fitView();
        layoutResetInFlightRef.current = false;
      }, 500);
    },
    getViewState: () => {
      try {
        const viewport = getViewport();
        const nodes = flowNodesRef.current || [];
        const node_positions = Object.fromEntries(
          nodes.map(n => [String(n.id).toLowerCase(), { x: n.position.x, y: n.position.y }])
        );
        return { viewport, node_positions };
      } catch {
        return null;
      }
    },
    setViewState: (state) => {
      if (!state) return;
      if (state.node_positions) {
        setSavedPositions(normalizePositions(state.node_positions));
      }
      if (state.viewport) {
        skipNextFitViewRef.current = true;
        setTimeout(() => setViewport(state.viewport!), 0);
      }
    }
  }), [fitView, getViewport, setViewport, setSavedPositions, normalizePositions]);

  useEffect(() => {
    flowNodesRef.current = flowNodes;
  }, [flowNodes]);

  useEffect(() => {
    if (!graphViewState) {
      return;
    }
    
    const hasPositions = !!graphViewState.node_positions && Object.keys(graphViewState.node_positions).length > 0;

    if (hasPositions && visibleNodes.length === 0) {
      return;
    }

    if (graphViewState.node_positions) {
      const normalized = normalizePositions(graphViewState.node_positions);
      setSavedPositions(normalized);
      
      const visibleIds = new Set(visibleNodes.map(n => String(n.id).toLowerCase()));
      const missing = Object.keys(normalized).filter(key => !visibleIds.has(key));
      const matched = Object.keys(normalized).filter(key => visibleIds.has(key));
      const logKey = JSON.stringify({ missing, visibleCount: visibleIds.size });
      if (missing.length > 0 && lastMissingLogRef.current !== logKey) {
        lastMissingLogRef.current = logKey;
      }
      
      setFlowNodes(prev => {
        return prev.map(n => {
          const pos = normalized[String(n.id).toLowerCase()];
          return pos ? { ...n, position: { x: pos.x, y: pos.y } } : n;
        });
      });
      
      // Mark that we just applied graphViewState positions
      justAppliedGraphViewRef.current = true;
      
      // Only call onGraphViewApplied after a small delay to let positions render
      setTimeout(() => {
        onGraphViewApplied?.();
      }, 50);
      
      if (hasPositions && matched.length === 0) {
        console.log("[GraphCanvas] Has positions but no matches, returning");
        return;
      }
    }

    if (graphViewState.viewport) {
      skipNextFitViewRef.current = true;
      setTimeout(() => setViewport(graphViewState.viewport!), 0);
    }
  }, [graphViewState, normalizePositions, onGraphViewApplied, setViewport, visibleNodes]);

  // Custom onNodesChange that recomputes edge handles when drag ends
  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      // Apply node changes first
      const updatedNodes = applyNodeChanges(changes, flowNodes);
      setFlowNodes(updatedNodes);

      // Check if any drag operation ended
      const dragEnded = changes.some(
        (change) =>
          change.type === "position" &&
          change.dragging === false
      );

      // Recompute edge handles and group bounds if drag ended
      if (dragEnded) {
        // Find which nodes were moved
        const movedNodeIds = new Set<string>();
        changes.forEach((change) => {
          if (change.type === "position" && change.dragging === false) {
            movedNodeIds.add(change.id);
          }
        });

        // Build absolute positions for updated nodes (account for parent groups)
        const absById = new Map<string, { x: number; y: number }>();
        for (const fn of updatedNodes) {
          if (!fn.parentNode) {
            absById.set(fn.id, fn.position);
          } else {
            const parent = updatedNodes.find(x => x.id === fn.parentNode);
            if (parent) {
              absById.set(fn.id, {
                x: parent.position.x + fn.position.x,
                y: parent.position.y + fn.position.y,
              });
            }
          }
        }

        if (movedNodeIds.size > 0) {
          setSavedPositions(prev => {
            const next = { ...prev };
            for (const id of movedNodeIds) {
              const abs = absById.get(id);
              if (abs) {
                next[String(id).toLowerCase()] = { x: abs.x, y: abs.y };
              }
            }
            return next;
          });
        }

        // Update groups if any moved nodes are members
        setGroups(prev => {
          return prev.map(g => {
            const hasMovedMember = g.memberIds.some(id => movedNodeIds.has(id));
            if (!hasMovedMember) return g;

            // Recalculate group bounds
            const membersWithAbs = g.memberIds
              .map(id => ({ id, pos: absById.get(id) }))
              .filter((x): x is { id: string; pos: { x: number; y: number } } => !!x.pos);

            if (membersWithAbs.length < 2) return g;

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

            const rect = {
              x: minX - GROUP_PAD,
              y: minY - GROUP_PAD,
              width: maxX - minX + GROUP_PAD * 2,
              height: maxY - minY + GROUP_PAD * 2,
            };

            const childPositions: Record<string, { x: number; y: number }> = {};
            for (const { id, pos } of membersWithAbs) {
              childPositions[id] = {
                x: pos.x - rect.x,
                y: pos.y - rect.y,
              };
            }

            return { ...g, rect, childPositions };
          });
        });

        const updatedEdges = composedEdges.map((edge) => {
          const sourceNode = updatedNodes.find((n) => n.id === edge.source);
          const targetNode = updatedNodes.find((n) => n.id === edge.target);

          if (!sourceNode || !targetNode) {
            return edge;
          }

          const { sourceHandle, targetHandle } = getEdgeHandles(
            sourceNode.position,
            targetNode.position
          );

          // Only update if handles changed
          if (
            sourceHandle !== edge.sourceHandle ||
            targetHandle !== edge.targetHandle
          ) {
            return { ...edge, sourceHandle, targetHandle };
          }
          return edge;
        });

        setFlowEdges(updatedEdges);
      }
    },
    [flowNodes, composedEdges, setFlowNodes, setFlowEdges]
  );

  // Sync composed nodes when they change (new/deleted nodes or group membership changes)
  useEffect(() => {
    setFlowNodes(composedNodes);
  }, [composedNodes, setFlowNodes]);

  useEffect(() => {
    setFlowEdges(composedEdges);
  }, [composedEdges, setFlowEdges]);

  useEffect(() => {
    if (skipNextFitViewRef.current) {
      skipNextFitViewRef.current = false;
      return;
    }
    setTimeout(() => fitView(), 100);
  }, [fitView]);

  const handleNodeClick = useCallback(
    (event: React.MouseEvent, node: Node) => {
      event.stopPropagation();
      if (node.type === "azureGroup") {
        setSelectedNodeIds([]);
        setSelectedGroupId(node.id);
        manualGroupSelectionRef.current = node.id;
        return;
      }
      const graphNode = visibleNodes.find(n => n.id === node.id);
      if (graphNode) onNodeSelected?.(node.id);
    },
    [visibleNodes, onNodeSelected]
  );

  const handleSelectionChange = useCallback((params: OnSelectionChangeParams) => {
    const ids = (params.nodes ?? []).filter(n => n.type !== "azureGroup").map(n => n.id);
    setSelectedNodeIds(ids);

    const selectedGroup = (params.nodes ?? []).find(n => n.type === "azureGroup");
    if (selectedGroup) {
      setSelectedGroupId(selectedGroup.id);
      manualGroupSelectionRef.current = selectedGroup.id;
      return;
    }

    if (manualGroupSelectionRef.current) {
      setSelectedGroupId(manualGroupSelectionRef.current);
      return;
    }

    setSelectedGroupId(null);
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
      if (node.type === "azureGroup") {
        const group = groups.find(g => g.id === node.id);
        if (!group) return;

        const deltaX = node.position.x - group.rect.x;
        const deltaY = node.position.y - group.rect.y;
        if (deltaX === 0 && deltaY === 0) return;

        const nextRect = {
          x: group.rect.x + deltaX,
          y: group.rect.y + deltaY,
          width: group.rect.width,
          height: group.rect.height,
        };

        setGroups(prev =>
          prev.map(g => (g.id === group.id ? { ...g, rect: nextRect } : g))
        );

        setSavedPositions(prev => {
          const next = { ...prev };
          for (const memberId of group.memberIds) {
            const rel = group.childPositions[memberId];
            if (!rel) continue;
            next[String(memberId).toLowerCase()] = {
              x: nextRect.x + rel.x,
              y: nextRect.y + rel.y,
            };
          }
          return next;
        });

        return;
      }
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

      const currentGroupId = groupMembership.get(node.id);

      // If node was in a group but dropped outside all groups, remove it from the group
      if (currentGroupId && !hit) {
        removeMemberFromLocalGroups(node.id, currentGroupId);
        await onRemoveNodeFromGroup?.({ nodeId: node.id, groupId: currentGroupId });
        return;
      }

      if (!hit) return;
      const targetGroupId = hit.id;
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
        onNodeDragStart?.(); // Close drawer after successful move
      } catch (e) {
        console.error("Failed to move node into group", e);
      } finally {
        setGroupBusy(false);
      }
    },
    [flowNodes, groupMembership, groupBusy, onMoveNodeToGroup, groups, removeMemberFromLocalGroups]
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
    manualGroupSelectionRef.current = null;
    setSelectedGroupId(null);
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
      <Controls>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          <button
            onClick={() => {
              setSavedPositions({});
              setTimeout(() => fitView(), 150);
            }}
            style={{
              cursor: "pointer",
              border: "none",
              background: "#fefefe",
              padding: 0,
              display: "flex",
              justifyContent: "center",
            }}
            onMouseEnter={e => {
              e.currentTarget.style.background = "#f3f2f1";
            }}
            onMouseLeave={e => {
              e.currentTarget.style.background = "#fefefe";
            }}
            title="Auto layout - Reset node positions"
          >
            <span style={{ 
              fontSize: 18, 
              fontWeight: 400, 
            }}>
              ↻
            </span>
          </button>
          <button
            onClick={() => props.onAiLayerEnabledChange?.(!props.aiLayerEnabled)}
            style={{
              cursor: "pointer",
              border: "none",
              background: "#fefefe",
              padding: 0,
              display: "flex",
              justifyContent: "center",
            }}
            title="Show AI annotations"
          >
            <span style={{ 
              fontSize: 8, 
              fontWeight: 400, 
              padding: "2px 6px", 
              background: props.aiLayerEnabled ? "#fdb913" : "transparent", 
              borderRadius: 12,
              color: "#000",
              border: "2px solid #000",
              transition: "background 0.2s ease",
            }}>
              AI
            </span>
          </button>
          <button
            onClick={() => props.onUserLayerEnabledChange?.(!props.userLayerEnabled)}
            style={{
              cursor: "pointer",
              border: "none",
              background: "#fefefe",
              padding: 0,
              display: "flex",
              justifyContent: "center",
            }}
            title="Show user overrides"
          >
            <span style={{ 
              fontSize: 8, 
              fontWeight: 400, 
              padding: "2px 6px", 
              background: props.userLayerEnabled ? "#7fba00" : "transparent", 
              borderRadius: 12,
              color: "#000",
              border: "2px solid #000",
              transition: "background 0.2s ease",
            }}>
              Ui
            </span>
          </button>
        </div>
      </Controls>
    </ReactFlow>
  );
});

GraphCanvas.displayName = 'GraphCanvas';

export default GraphCanvas;
