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
  from_id: string;
  to_id: string;
  relationship: string;
  confidence: number;
  source: string; // edge origin from backend (arg/manual/heuristic/etc)
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
  from_id: string;
  to_id: string;
  relationship: string;
  confidence?: number;
  reason?: string;
  status?: string;
  source?: string;
}

export interface RawGraphSnapshot {
  nodes: RawGraphNode[];
  edges: RawGraphEdge[];
  llm_annotations?: {
    nodes?: LlmNodeAnnotation[];
    edges?: LlmEdgeSuggestion[];
  };
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

export async function fetchWorkloadGraph(workloadId: WorkloadId, includeLlm: boolean): Promise<RawGraphSnapshot> {
  const qs = includeLlm ? "?include_llm=true" : "";
  return await apiJson<RawGraphSnapshot>(workloadPath(workloadId, `/graph${qs}`));
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

export async function createManualEdge(
  workloadId: WorkloadId,
  payload: { from_id: string; to_id: string; relationship: string }
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
    group_id?: string | null;
    group_label?: string | null;
  }
): Promise<void> {
  await apiNoBody(workloadPath(workloadId, `/nodes/${encodeURIComponent(nodeId)}`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function resetNode(workloadId: WorkloadId, nodeId: string): Promise<void> {
  await apiNoBody(workloadPath(workloadId, `/nodes/${encodeURIComponent(nodeId)}`), { method: "DELETE" });
}

export async function patchNodeCriticality(
  workloadId: WorkloadId,
  nodeId: string,
  score: number
): Promise<void> {
  await apiNoBody(workloadPath(workloadId, `/nodes/${encodeURIComponent(nodeId)}/criticality`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ score }),
  });
}

export async function resetNodeCriticality(workloadId: WorkloadId, nodeId: string): Promise<void> {
  await apiNoBody(workloadPath(workloadId, `/nodes/${encodeURIComponent(nodeId)}/criticality`), { method: "DELETE" });
}
