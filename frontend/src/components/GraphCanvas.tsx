import React, { useEffect, useRef } from "react";
import Cytoscape from "cytoscape";
import dagre from "cytoscape-dagre";
// @ts-ignore - no types for edgehandles
import edgehandles from "cytoscape-edgehandles";

Cytoscape.use(dagre);
// avoid double registration in HMR
// @ts-ignore - plugin registration
if (!(Cytoscape as any).prototype.edgehandles) {
  Cytoscape.use(edgehandles);
}

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
  origin?: string; // data source for the edge (manual, arg, heuristic)
}

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
  onEdgeSelected?: (edge: GraphEdge | null) => void;
  onNodeSelected?: (nodeId: string | null) => void;
  maxImportance?: number;
  linkMode?: boolean;
  onEdgeCreate?: (sourceId: string, targetId: string) => void;
  onNodeRename?: (nodeId: string) => void;
}
const GraphCanvas: React.FC<Props> = ({
  nodes,
  edges,
  onEdgeSelected,
  onNodeSelected,
  maxImportance = 1,
  linkMode = false,
  onEdgeCreate,
  onNodeRename,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Cytoscape.Core | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    // Filter nodes by architectural importance (progressive disclosure)
    const visibleNodes = nodes.filter(
      n => (n.metadata?.importance ?? 3) <= maxImportance
    );

    const visibleNodeIds = new Set(visibleNodes.map(n => n.id));

    const visibleEdges = edges.filter(
      e => visibleNodeIds.has(e.source) && visibleNodeIds.has(e.target)
    );

    const elements: Cytoscape.ElementDefinition[] = [
      ...visibleNodes.map(n => ({
        data: {
          id: n.id,
          label: `${n.metadata?.icon || ""} ${n.name || n.id.split('/').pop() || 'unknown'}\n${n.metadata?.criticality_stars ? n.metadata.criticality_stars : ''}`,
          type: n.type,
          parent: n.metadata?.parent_id,
          origin: (n as any).source ?? n.metadata?.source ?? "arg",
          synthetic: n.metadata?.synthetic ? "true" : "false",
          ai_annotation: n.metadata?.ai_annotation ? "true" : "false",
          ai_tooltip: n.metadata?.ai_tooltip,
          criticality_score: n.metadata?.criticality_score,
          criticality_stars: n.metadata?.criticality_stars,
            ...(n.metadata?.shape_override ? { shape_override: n.metadata.shape_override } : {}),
            ...(n.metadata?.color_override ? { color_override: n.metadata.color_override } : {}),
        }
      })),
      ...visibleEdges.map(e => {
        const sourceId = (e as any).from_id ?? (e as any).source;
        const targetId = (e as any).to_id ?? (e as any).target;
        const origin = (e as any).origin ?? (e as any).edge_origin ?? (e as any).raw_source ?? (e as any).source_origin ?? (e as any).ingestion ?? (e as any).source ?? "arg";
        const isAi = origin === "llm";

        return {
          data: {
            id: e.id,
            source: sourceId,
            target: targetId,
            label: e.relationship || "",
            confidence: e.confidence ?? 1,
            status: e.status ?? "proposed",
            origin,
            is_ai: isAi ? "true" : "false"
          }
        };
      })
    ];

    cyRef.current = Cytoscape({
      container: containerRef.current,
      elements,
      layout: {
        name: "dagre",
        rankDir: "TB",
        nodeSep: 80,
        rankSep: 150,
        padding: 60,
        minLen: (edge: any) => 1
      } as any,
      style: [
        // Base node - enhanced visual style
        {
          selector: "node[label]",
          style: {
            "label": "data(label)",
            "font-size": "11px",
            "font-family": "Segoe UI, system-ui, sans-serif",
            "font-weight": 600,
            "text-wrap": "wrap",
            "text-max-width": "100px",
            "text-valign": "center",
            "text-halign": "center",
            "background-color": "#ffffff",
            "color": "#323130",
            "shape": "round-rectangle",
            "min-width": "90px",
            "width": "label",
            "height": "60px",
            "padding": "12px",
            "border-width": 1.8,
            "border-color": "#8a8886"
          }
        },

        // Node color override
        {
          selector: "node[color_override]",
          style: {
            "background-color": "data(color_override)"
          }
        },

        // Node shape override
        {
          selector: "node[shape_override]",
          style: {
            // cytoscape typings are stricter; data-mapped shape is valid at runtime
            "shape": "data(shape_override)" as any
          }
        },

        // AI-annotated nodes (no visual change; tooltip handled in JS)

        // Origin-based node coloring
        {
          selector: "node[origin = 'manual']",
          style: {
            "background-color": "#16a34a",
            "border-color": "#0f5c2c"
          }
        },
        {
          selector: "node[synthetic = 'true']",
          style: {
            "background-color": "#7c7c7c",
            "border-color": "#4b4b4b"
          }
        },

        // Compute (VM, AKS) - Azure Blue
        {
          selector: "node[type = 'aks'], node[type = 'vm']",
          style: {
            "background-color": "#0078d4",
            "color": "#ffffff",
            "border-color": "#004578"
          }
        },

        // VNet - Network Blue
        {
          selector: "node[type = 'vnet']",
          style: {
            "background-color": "#50e6ff",
            "color": "#000000",
            "border-color": "#0078d4",
            "shape": "rectangle"
          }
        },

        // Subnet - Lighter Network
        {
          selector: "node[type = 'subnet']",
          style: {
            "background-color": "#b3e0ff",
            "color": "#000000",
            "border-color": "#0078d4",
            "shape": "rectangle"
          }
        },

        // Network resources - Teal
        {
          selector: "node[type = 'network'], node[type = 'nic'], node[type = 'nsg'], node[type = 'pip']",
          style: {
            "background-color": "#00bcf2",
            "color": "#000000",
            "border-color": "#0078d4"
          }
        },

        // PaaS (SQL, Storage, KeyVault) - Green
        {
          selector: "node[type = 'sql'], node[type = 'storage'], node[type = 'keyvault']",
          style: {
            "background-color": "#00ad56",
            "color": "#ffffff",
            "border-color": "#008542",
            "shape": "ellipse"
          }
        },

        // Private Endpoint - Purple
        {
          selector: "node[type = 'private_endpoint']",
          style: {
            "background-color": "#5c2d91",
            "color": "#ffffff",
            "border-color": "#3c1a5b"
          }
        },

        // Compound nodes (VNet / Subnet containers)
        {
          selector: ":parent[label]",
          style: {
            "background-opacity": 0.08,
            "border-width": 1,
            "border-color": "#5EA0EF",
            "padding": "20px",
            "label": "data(label)",
            "text-valign": "top",
            "text-halign": "center",
            "font-size": "10px",
            "color": "#9CDCFE"
          }
        },

        // Edges - enhanced with better visibility
        {
          selector: "edge[label]",
          style: {
            "curve-style": "bezier",
            "width": 2.2,
            "line-color": "#9AA0A6",
            "target-arrow-shape": "triangle",
            "target-arrow-color": "#9AA0A6",
            "target-arrow-fill": "filled",
            "label": "data(label)",
            "font-size": "8px",
            "text-rotation": "autorotate",
            "text-margin-y": -8,
            "opacity": 0.88
          }
        },

        // Manual edges: bold green
        {
          selector: "edge[origin = 'manual'][label]",
          style: {
            "line-color": "#22c55e",
            "target-arrow-color": "#22c55e",
            "width": 3,
            "opacity": 1
          }
        },

        // Heuristic edges: muted dashed
        {
          selector: "edge[origin = 'heuristic'][label]",
          style: {
            "line-style": "dashed",
            "line-color": "#9AA0A6",
            "target-arrow-color": "#9AA0A6",
            "opacity": 0.7
          }
        },

        // LLM-suggested edges: dotted amber
        {
          selector: "edge[origin = 'llm'][label]",
          style: {
            "line-style": "dotted",
            "line-color": "#f59e0b",
            "target-arrow-color": "#f59e0b",
            "opacity": 0.8
          }
        },

        // AI icon badge on LLM edges
        {
          selector: "edge[is_ai = 'true']",
          style: {
            "source-label": "🤖",
            "source-text-offset": 10,
            "source-text-margin-x": -6,
            "source-text-margin-y": -4,
            "font-size": "10px"
          }
        },

        // ARG edges: bright azure
        {
          selector: "edge[origin = 'arg'][label]",
          style: {
            "line-color": "#5EA0EF",
            "target-arrow-color": "#5EA0EF",
            "width": 2.5
          }
        },

        // Accepted edges
        {
          selector: "edge[status = 'accepted'][origin != 'manual'][label]",
          style: {
            "line-color": "#0078D4",
            "target-arrow-color": "#0078D4",
            "width": 3
          }
        },

        // Proposed edges: dotted
        {
          selector: "edge[status = 'proposed'][label]",
          style: {
            "line-style": "dotted",
            "opacity": 0.75
          }
        },

        // Low confidence
        {
          selector: "edge[confidence < 0.8][label]",
          style: {
            "line-style": "dashed"
          }
        }
      ]
    });

    const cy = cyRef.current;

    // Create a lightweight custom tooltip inside the container (no portal needed)
    if (containerRef.current && !tooltipRef.current) {
      const tip = document.createElement("div");
      tip.style.position = "absolute";
      tip.style.pointerEvents = "none";
      tip.style.background = "#1a1a1a";
      tip.style.color = "#f5f5f5";
      tip.style.padding = "10px 12px";
      tip.style.border = "1px solid #333";
      tip.style.borderRadius = "6px";
      tip.style.boxShadow = "0 8px 24px rgba(0,0,0,0.35)";
      tip.style.fontFamily = "Segoe UI, system-ui, sans-serif";
      tip.style.fontSize = "12px";
      tip.style.lineHeight = "1.4";
      tip.style.zIndex = "200";
      tip.style.display = "none";
      tip.style.maxWidth = "280px";
      containerRef.current.appendChild(tip);
      tooltipRef.current = tip;
    }

    // Drag-to-link when linkMode is on
    let eh: any | null = null;
    let startHandler: any = null;
    if (linkMode) {
      eh = (cy as any).edgehandles({
        handleNodes: "node",
        handleSize: 10,
        handleColor: "#22c55e",
        handleOutlineColor: "#0f172a",
        handleOutlineWidth: 2,
        edgeType: () => "flat",
        loopAllowed: () => false,
        noEdgeEventsInDraw: true,
        disableBrowserGestures: true,
        preview: true
      });

      // prevent moving nodes while in link mode; treat drag as link gesture
      cy.nodes().ungrabify();

      const onComplete = (event: any, sourceNode: any, targetNode: any, addedEdge: any) => {
        if (addedEdge) addedEdge.remove();
        onEdgeCreate?.(sourceNode.id(), targetNode.id());
      };

      startHandler = (evt: any) => {
        if (!eh || !evt.target || evt.target.isEdge && evt.target.isEdge()) return;
        eh.start(evt.target);
      };

      cy.on("ehcomplete", onComplete);
      cy.on("tapstart", "node", startHandler);
      eh.enable();

      // cleanup edgehandles on destroy
      cy.one("destroy", () => {
        cy.off("ehcomplete", onComplete);
        cy.off("tapstart", "node", startHandler);
        if (eh && eh.destroy) {
          eh.destroy();
          eh = null;
        }
      });
    }

    cy.on("tap", "node", evt => {
      const nodeId = evt.target.id();
      onNodeSelected?.(nodeId);

      if (linkMode) {
        // visual hint: briefly highlight node when in link mode
        evt.target.animate({ style: { "border-width": 3, "border-color": "#22c55e" } }, { duration: 120 }).then(() => {
          evt.target.animate({ style: { "border-width": 1, "border-color": "#0b3b7a" } }, { duration: 120 });
        });
      }
    });

    cy.on("dbltap", "node", evt => {
      const nodeId = evt.target.id();
      onNodeRename?.(nodeId);
    });

    // Tooltip on hover for AI annotation details (custom styled div)
    cy.on("mouseover", "node", evt => {
      const tooltip = evt.target.data("ai_tooltip");
      const tip = tooltipRef.current;
      if (!tooltip || !tip) return;
      tip.innerHTML = tooltip.replace(/\n/g, "<br/>");
      tip.style.display = "block";
      const pos = evt.renderedPosition || evt.position;
      // Position with slight offset; clamp within container width/height
      const containerRect = containerRef.current?.getBoundingClientRect();
      if (containerRect) {
        const offsetX = 14;
        const offsetY = 14;
        const left = Math.min(Math.max(pos.x + offsetX, 4), containerRect.width - 4);
        const top = Math.min(Math.max(pos.y + offsetY, 4), containerRect.height - 4);
        tip.style.left = `${left}px`;
        tip.style.top = `${top}px`;
      }
    });

    cy.on("mousemove", "node", evt => {
      const tip = tooltipRef.current;
      if (!tip || tip.style.display === "none") return;
      const pos = evt.renderedPosition || evt.position;
      const containerRect = containerRef.current?.getBoundingClientRect();
      if (containerRect) {
        const offsetX = 14;
        const offsetY = 14;
        const left = Math.min(Math.max(pos.x + offsetX, 4), containerRect.width - 4);
        const top = Math.min(Math.max(pos.y + offsetY, 4), containerRect.height - 4);
        tip.style.left = `${left}px`;
        tip.style.top = `${top}px`;
      }
    });

    cy.on("mouseout", "node", () => {
      const tip = tooltipRef.current;
      if (tip) {
        tip.style.display = "none";
      }
    });

    // Edge click → inspect
    cy.on("tap", "edge", evt => {
      const data = evt.target.data();
      onEdgeSelected?.({
        id: data.id,
        source: data.source,
        target: data.target,
        relationship: data.label,
        confidence: data.confidence,
        status: data.status,
        origin: data.origin
      });
    });

    // Background click → close sidebar
    cy.on("tap", evt => {
      if (evt.target === cy) {
        onEdgeSelected?.(null);
        onNodeSelected?.(null);
      }
    });

    return () => {
      if (tooltipRef.current && containerRef.current?.contains(tooltipRef.current)) {
        containerRef.current.removeChild(tooltipRef.current);
        tooltipRef.current = null;
      }
      cy.destroy();
      cyRef.current = null;
    };
  }, [nodes, edges, onEdgeSelected, onNodeSelected, maxImportance, linkMode, onEdgeCreate, onNodeRename]);

  return (
    <div
      ref={containerRef}
      style={{
        width: "100%",
        height: "100%",
        background: "#ffffff"
      }}
    />
  );
};

export default GraphCanvas;