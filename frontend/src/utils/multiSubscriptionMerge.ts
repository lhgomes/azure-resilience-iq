/**
 * Utilities for merging graph, resilience, and zonal data from multiple subscriptions
 */

import type { GraphSnapshot } from "../domain/graphView";
import type { GraphNode, GraphEdge } from "../components/GraphCanvasReactflow";
import type { ZonalResilienceResponse } from "../api/resilience";
import type { LlmNodeAnnotation, LlmEdgeSuggestion, NodeGroup } from "../api/workloads";

/**
 * Merge multiple graph snapshots into a single one
 * - Deduplicates nodes by ID
 * - Preserves all edges
 * - Adds subscription context to nodes
 */
export function mergeGraphSnapshots(
  graphs: Array<{ subscriptionId: string; graph: GraphSnapshot }>,
): GraphSnapshot {
  const mergedNodes: Record<string, GraphNode> = {};
  const mergedEdges: GraphEdge[] = [];
  const mergedLlmNodes = new Map<string, LlmNodeAnnotation>();
  const mergedLlmEdges = new Map<string, LlmEdgeSuggestion>();
  const mergedNodeOverrides: Record<string, Record<string, unknown>> = {};
  const mergedEdgeOverrides: Record<string, Record<string, unknown>> = {};
  const mergedGroups = new Map<string, NodeGroup>();

  graphs.forEach(({ subscriptionId, graph }) => {
    // Merge nodes, preserving first occurrence (they should be identical across subscriptions anyway)
    graph.nodes.forEach(node => {
      if (!mergedNodes[node.id]) {
        // Add subscription context to node if not already present
        const nodeSub = (node as any)?.subscription_id;
        mergedNodes[node.id] = {
          ...node,
          subscription_id: nodeSub || subscriptionId,
        } as GraphNode;
      }
    });

    // Merge edges, adjusting IDs if needed to avoid collisions
    graph.edges.forEach(edge => {
      const edgeSub = (edge as any)?.subscription_id;
      mergedEdges.push({
        ...edge,
        subscription_id: edgeSub || subscriptionId,
      } as GraphEdge);
    });

    // Merge LLM annotations
    if (graph.llm_annotations?.nodes) {
      graph.llm_annotations.nodes.forEach(nodeAnn => {
        if (!nodeAnn?.node_id) return;
        if (!mergedLlmNodes.has(nodeAnn.node_id)) {
          mergedLlmNodes.set(nodeAnn.node_id, nodeAnn);
        }
      });
    }

    if (graph.llm_annotations?.edges) {
      graph.llm_annotations.edges.forEach(edgeAnn => {
        if (!edgeAnn?.source || !edgeAnn?.target || !edgeAnn?.relationship) return;
        const key = `${edgeAnn.source}|${edgeAnn.target}|${edgeAnn.relationship}`;
        if (!mergedLlmEdges.has(key)) {
          // Add subscription context to LLM edge annotation
          mergedLlmEdges.set(key, {
            ...edgeAnn,
            subscription_id: subscriptionId,
          } as any);
        }
      });
    }

    // Merge overrides
    if (graph.node_overrides) {
      Object.assign(mergedNodeOverrides, graph.node_overrides);
    }
    if (graph.edge_overrides) {
      Object.assign(mergedEdgeOverrides, graph.edge_overrides);
    }

    // Merge groups (by ID)
    if (graph.groups) {
      graph.groups.forEach(group => {
        if (!group?.id) return;
        const existing = mergedGroups.get(group.id);
        if (!existing) {
          mergedGroups.set(group.id, group);
          return;
        }

        const mergedMembers = Array.from(new Set([...(existing.nodes || []), ...(group.nodes || [])]));
        mergedGroups.set(group.id, {
          ...existing,
          name: existing.name || group.name,
          nodes: mergedMembers,
        });
      });
    }
  });

  return {
    nodes: Object.values(mergedNodes),
    edges: mergedEdges,
    llm_annotations:
      mergedLlmNodes.size || mergedLlmEdges.size
        ? {
            nodes: Array.from(mergedLlmNodes.values()),
            edges: Array.from(mergedLlmEdges.values()),
          }
        : undefined,
    node_overrides: Object.keys(mergedNodeOverrides).length > 0 ? mergedNodeOverrides : undefined,
    edge_overrides: Object.keys(mergedEdgeOverrides).length > 0 ? mergedEdgeOverrides : undefined,
    groups: mergedGroups.size > 0 ? Array.from(mergedGroups.values()) : undefined,
  };
}

/**
 * Merge resilience evaluations from multiple subscriptions
 */
export function mergeResilienceEvaluations(
  evaluations: Array<{ subscriptionId: string; evaluations: Record<string, any> }>,
): Record<string, any> {
  const merged: Record<string, any> = {};

  evaluations.forEach(({ subscriptionId, evaluations: evalData }) => {
    Object.entries(evalData).forEach(([resourceId, evaluation]) => {
      if (!merged[resourceId]) {
        merged[resourceId] = {
          ...evaluation,
          subscription_id: evaluation.subscription_id || subscriptionId,
        };
      } else {
        // If resource exists in multiple subscriptions, merge the checks
        const current = merged[resourceId];
        const currentChecks = (current as any).checks || (current as any).findings || [];
        const newChecks = (evaluation as any).checks || (evaluation as any).findings || [];

        const combinedChecks = [
          ...currentChecks,
          ...newChecks.filter(
            (newCheck: any) =>
              !currentChecks.some(
                (existingCheck: any) =>
                  existingCheck?.resilience_check_id === newCheck?.resilience_check_id,
              ),
          ),
        ];

        if ((evaluation as any).checks) {
          (merged[resourceId] as any).checks = combinedChecks;
        } else if ((evaluation as any).findings) {
          (merged[resourceId] as any).findings = combinedChecks;
        }

        // Recalculate counts
        const passed = combinedChecks.filter((c: any) => c.status === "pass").length;
        const failed = combinedChecks.filter((c: any) => c.status === "fail").length;

        (merged[resourceId] as any).passed_checks = passed;
        (merged[resourceId] as any).failed_checks = failed;
        (merged[resourceId] as any).total_checks = combinedChecks.length;
      }
    });
  });

  return merged;
}

/**
 * Merge zonal resilience data from multiple subscriptions
 */
export function mergeZonalResilienceData(
  dataList: Array<{ subscriptionId: string; data: ZonalResilienceResponse }>,
): ZonalResilienceResponse {
  if (dataList.length === 0) {
    return {
      subscription_id: "multi",
      analysis_timestamp: new Date().toISOString(),
      resources: [],
    };
  }

  if (dataList.length === 1) {
    return dataList[0].data;
  }

  const resourceMap = new Map<string, ZonalResilienceResponse["resources"][number]>();
  let latestTimestamp = dataList[0].data.analysis_timestamp;

  dataList.forEach(({ data }) => {
    if (data.analysis_timestamp > latestTimestamp) {
      latestTimestamp = data.analysis_timestamp;
    }

    (data.resources || []).forEach(resource => {
      if (!resource?.resource_id) return;
      if (!resourceMap.has(resource.resource_id)) {
        resourceMap.set(resource.resource_id, resource);
      }
    });
  });

  return {
    subscription_id: "multi",
    analysis_timestamp: latestTimestamp,
    resources: Array.from(resourceMap.values()),
  };
}
