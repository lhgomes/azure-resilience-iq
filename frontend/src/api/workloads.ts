export type WorkloadId = string;

export interface RawGraphNode {
  id: string;
  type: string;
  name: string;
  metadata?: Record<string, unknown>;
  source?: string;
  state?: string;
}

export type EdgeStatus = "proposed" | "accepted" | "rejected";

export interface RawGraphEdge {
  id: string;
  source: string; // edge start (ReactFlow naming)
  target: string; // edge end (ReactFlow naming)
  relationship: string;
  confidence: number;
  origin: string; // edge origin from backend (arg/manual/heuristic/etc)
  evidence?: Array<Record<string, unknown>>;
  status?: EdgeStatus;
}

export interface LlmNodeAnnotationPayload {
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

export interface LlmNodeAnnotation {
  node_id: string;
  annotations: LlmNodeAnnotationPayload;
}

export interface LlmEdgeSuggestion {
  source: string;
  target: string;
  relationship: string;
  confidence?: number;
  reason?: string;
  status?: string;
  origin?: string;
}

export interface NodeGroup {
  id: string;
  name: string;
  nodes: string[];
}

export interface RawGraphSnapshot {
  nodes: RawGraphNode[];
  edges: RawGraphEdge[];
  llm_annotations?: {
    nodes?: LlmNodeAnnotation[];
    edges?: LlmEdgeSuggestion[];
  };
  node_overrides?: Record<string, Record<string, unknown>>;
  edge_overrides?: Record<string, Record<string, unknown>>;
  groups?: NodeGroup[];
}

async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let bodyText: string | undefined;
    try {
      bodyText = await res.text();
    } catch {
      bodyText = undefined;
    }
    const suffix = bodyText ? `: ${bodyText}` : "";
    throw new Error(`Request failed (${res.status})${suffix}`);
  }
  return (await res.json()) as T;
}

async function apiNoBody(path: string, init?: RequestInit): Promise<void> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let bodyText: string | undefined;
    try {
      bodyText = await res.text();
    } catch {
      bodyText = undefined;
    }
    const suffix = bodyText ? `: ${bodyText}` : "";
    throw new Error(`Request failed (${res.status})${suffix}`);
  }
}

export function workloadPath(workloadId: WorkloadId, suffix: string): string {
  return `/api/workloads/${encodeURIComponent(workloadId)}${suffix}`;
}

export async function fetchWorkloadGraph(workloadId: WorkloadId): Promise<RawGraphSnapshot> {
  return await apiJson<RawGraphSnapshot>(workloadPath(workloadId, `/graph`));
}

export async function acceptEdge(workloadId: WorkloadId, edgeId: string): Promise<void> {
  await apiNoBody(workloadPath(workloadId, `/edges/${encodeURIComponent(edgeId)}/accept`), { method: "POST" });
}

export async function rejectEdge(workloadId: WorkloadId, edgeId: string): Promise<void> {
  await apiNoBody(workloadPath(workloadId, `/edges/${encodeURIComponent(edgeId)}/reject`), { method: "POST" });
}

export async function deleteEdge(workloadId: WorkloadId, edgeId: string): Promise<void> {
  await apiNoBody(workloadPath(workloadId, `/edges/${encodeURIComponent(edgeId)}`), { method: "DELETE" });
}

export async function reverseEdgeDirection(workloadId: WorkloadId, edgeId: string): Promise<{ edge?: RawGraphEdge; old_edge_id?: string } & Record<string, unknown>> {
  return await apiJson(workloadPath(workloadId, `/edges/${encodeURIComponent(edgeId)}/reverse`), { method: "POST" });
}

export async function createManualEdge(
  workloadId: WorkloadId,
  payload: { source: string; target: string; relationship: string }
): Promise<{ edge?: RawGraphEdge } & Record<string, unknown>> {
  return await apiJson(workloadPath(workloadId, "/edges"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function patchNode(
  workloadId: WorkloadId,
  nodeId: string,
  payload: {
    name?: string;
    layer?: number | null;
    color?: string | null;
    icon?: string | null;
    criticality_score?: number | null;
  }
): Promise<void> {
  // URL-encode nodeId so slashes don't break the path, `:path` converter will decode it
  await apiNoBody(`/api/workloads/${encodeURIComponent(workloadId)}/nodes/${encodeURIComponent(nodeId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function resetNode(workloadId: WorkloadId, nodeId: string): Promise<void> {
  // URL-encode nodeId so slashes don't break the path, `:path` converter will decode it
  await apiNoBody(`/api/workloads/${encodeURIComponent(workloadId)}/nodes/${encodeURIComponent(nodeId)}`, { method: "DELETE" });
}

// Group API functions
export async function createGroup(
  workloadId: WorkloadId,
  payload: { id: string; name: string; nodes: string[] }
): Promise<NodeGroup> {
  return await apiJson(`/api/workloads/${encodeURIComponent(workloadId)}/groups`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function updateGroup(
  workloadId: WorkloadId,
  groupId: string,
  payload: { name?: string; nodes?: string[] }
): Promise<NodeGroup> {
  return await apiJson(`/api/workloads/${encodeURIComponent(workloadId)}/groups/${encodeURIComponent(groupId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteGroup(workloadId: WorkloadId, groupId: string): Promise<void> {
  await apiNoBody(`/api/workloads/${encodeURIComponent(workloadId)}/groups/${encodeURIComponent(groupId)}`, {
    method: "DELETE",
  });
}

export async function addNodeToGroup(
  workloadId: WorkloadId,
  groupId: string,
  nodeId: string
): Promise<void> {
  await apiNoBody(`/api/workloads/${encodeURIComponent(workloadId)}/groups/${encodeURIComponent(groupId)}/nodes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ node_id: nodeId }),
  });
}

export async function removeNodeFromGroup(
  workloadId: WorkloadId,
  groupId: string,
  nodeId: string
): Promise<void> {
  await apiNoBody(
    `/api/workloads/${encodeURIComponent(workloadId)}/groups/${encodeURIComponent(groupId)}/nodes/${encodeURIComponent(nodeId)}`,
    { method: "DELETE" }
  );
}
