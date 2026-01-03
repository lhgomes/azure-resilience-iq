import React, { useEffect, useCallback, useMemo } from "react";
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
  ConnectionMode
} from "reactflow";
import "reactflow/dist/style.css";
import dagre from "dagre";
import AzureNode from "./AzureNode";
import AzureEdge from "./AzureEdge";

export interface GraphNode {
  id: string;
  name: string;
  type: string;
  metadata?: Record<string, any>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  relationship: string;
  confidence?: number;
  status?: string;
  origin?: string;
}

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
  onEdgeSelected?: (edge: GraphEdge | null) => void;
  onNodeSelected?: (nodeId: string | null) => void;
  maxImportance?: number;
  onEdgeCreate?: (sourceId: string, targetId: string) => void;
  onNodeRename?: (nodeId: string) => void;
}

const nodeTypes: NodeTypes = { azure: AzureNode };
const edgeTypes: EdgeTypes = { azure: AzureEdge as any };

const GraphCanvas: React.FC<Props> = ({
  nodes: nodesProp,
  edges: edgesProp,
  onEdgeSelected,
  onNodeSelected,
  maxImportance = 1,
  onEdgeCreate,
  onNodeRename,
}) => {
  const { fitView } = useReactFlow();

  // Filter nodes by importance
  const visibleNodes = useMemo(() => {
    return nodesProp.filter(n => (n.metadata?.importance ?? 3) <= maxImportance);
  }, [nodesProp, maxImportance]);

  const visibleNodeIds = useMemo(
    () => new Set(visibleNodes.map(n => n.id)),
    [visibleNodes]
  );

  const visibleEdges = useMemo(() => {
    return edgesProp.filter(
      e => visibleNodeIds.has(e.source) && visibleNodeIds.has(e.target)
    );
  }, [edgesProp, visibleNodeIds]);

  // Convert to Reactflow format
  const rfNodes: Node[] = useMemo(() => {
    return visibleNodes.map(n => ({
      id: n.id,
      data: {
        label: n.name || n.id.split("/").pop() || "unknown",
        icon: n.metadata?.icon,
        type: n.type,
        criticality_stars: n.metadata?.criticality_stars,
        color_override: n.metadata?.color_override,
        shape_override: n.metadata?.shape_override,
        ai_annotation: !!n.metadata?.ai_annotation,
        ai_tooltip: n.metadata?.ai_tooltip,
      },
      type: "azure",
      position: { x: 0, y: 0 }, // Will be set by layout
      connectable: true,
    }));
  }, [visibleNodes]);

  const rfEdges: Edge[] = useMemo(() => {
    return visibleEdges.map(e => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type: "azure",
      data: {
        label: e.relationship,
        origin: e.origin,
        status: e.status,
        confidence: e.confidence,
      },
      markerEnd: { type: MarkerType.ArrowClosed },
    }));
  }, [visibleEdges]);

  // Layout using Dagre
  const layoutedNodes = useMemo(() => {
    if (rfNodes.length === 0) return rfNodes;

    const g = new dagre.graphlib.Graph();
    g.setGraph({ 
      rankdir: "TB", 
      nodesep: 100, 
      ranksep: 180,
      marginx: 40,
      marginy: 40
    });
    g.setDefaultEdgeLabel(() => ({}));

    rfNodes.forEach(node => {
      g.setNode(node.id, { width: 140, height: 100 });
    });

    rfEdges.forEach(edge => {
      g.setEdge(edge.source, edge.target);
    });

    dagre.layout(g);

    return rfNodes.map(node => {
      const pos = g.node(node.id);
      if (!pos) {
        return { ...node, position: { x: 0, y: 0 } };
      }
      return {
        ...node,
        position: { x: pos.x - 70, y: pos.y - 50 },
      };
    });
  }, [rfNodes, rfEdges]);

  const [flowNodes, setFlowNodes, onNodesChange] = useNodesState(layoutedNodes);
  const [flowEdges, setFlowEdges, onEdgesChange] = useEdgesState(rfEdges);

  // Update when layout changes
  useEffect(() => {
    setFlowNodes(layoutedNodes);
  }, [layoutedNodes, setFlowNodes]);

  useEffect(() => {
    setFlowEdges(rfEdges);
  }, [rfEdges, setFlowEdges]);

  // Fit view on first load
  useEffect(() => {
    setTimeout(() => fitView(), 100);
  }, [fitView]);

  const handleNodeClick = useCallback(
    (event: React.MouseEvent, node: Node) => {
      event.stopPropagation();
      const graphNode = visibleNodes.find(n => n.id === node.id);
      if (graphNode) {
        onNodeSelected?.(node.id);
      }
    },
    [visibleNodes, onNodeSelected]
  );

  const handleEdgeClick = useCallback(
    (event: React.MouseEvent, edge: Edge) => {
      event.stopPropagation();
      const graphEdge = visibleEdges.find(e => e.id === edge.id);
      if (graphEdge) {
        onEdgeSelected?.(graphEdge);
      }
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
        onEdgeCreate?.(connection.source, connection.target);
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
      onEdgeClick={handleEdgeClick}
      onPaneClick={handlePaneClick}
      onConnect={handleConnect}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      connectionMode={ConnectionMode.Loose}
      fitView
    >
      <Controls />
    </ReactFlow>
  );
};

export default GraphCanvas;
