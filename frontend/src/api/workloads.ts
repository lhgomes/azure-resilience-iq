export type SubscriptionId = string;

export interface SubscriptionInfo {
  id: string;
  name: string;
  resource_count?: number;
  conversation_id?: string | null;
}

export interface DiscoverableSubscriptionInfo {
  id: string;
  name: string;
  state: string;
  mapped: boolean;
}

export interface SubscriptionMappingRequest {
  resource_groups?: string[];
  tags?: Record<string, string>;
}

export interface SubscriptionMappingStage {
  name: "collector" | "resilience" | "llm" | string;
  status: "pending" | "running" | "completed" | "failed" | string;
  started_at?: string;
  finished_at?: string;
  returncode?: number;
  stdout_tail?: string;
  stderr_tail?: string;
}

export interface SubscriptionMappingStatus {
  subscription_id: string;
  status: "idle" | "running" | "completed" | "failed" | string;
  current_stage?: string | null;
  progress?: number;
  message?: string;
  filters?: {
    resource_groups?: string[];
    tags?: Record<string, string>;
  };
  stages?: SubscriptionMappingStage[];
  started_at?: string;
  finished_at?: string;
}

export interface TerraformUploadResponse {
  subscription_id: string;
  subscription_name: string;
  resource_count: number;
  edge_count: number;
  message: string;
}

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
  criticality_weight?: number;
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
  resilience_evaluations?: {
    evaluations: Record<string, any>;
  };
  resilience_overrides?: Record<string, any>;
}

export type WorkloadViewLevel = "overview" | "network" | "full";

export interface WorkloadViewState {
  selected_subscriptions: string[];
  view_level: WorkloadViewLevel;
  ai_layer_enabled: boolean;
  user_layer_enabled: boolean;
  resource_group_filter: string[];
  service_filter: string[];
  expanded_categories: string[];
  show_legend: boolean;
  graph_view?: {
    viewport?: { x: number; y: number; zoom: number };
    node_positions?: Record<string, { x: number; y: number }>;
  };
}

export interface WorkloadSummary {
  workload_id: string;
  name: string;
  created_at: string;
  updated_at: string;
  conversation_id?: string | null;
}

export interface WorkloadRecord extends WorkloadSummary {
  view_state: WorkloadViewState;
}

export interface ApiRequestError extends Error {
  code?: string;
  status?: number;
}

async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    let code: string | undefined;

    try {
      const json = await res.json();
      const detail = json?.detail;

      if (typeof detail === "string") {
        message = detail;
      } else if (detail && typeof detail === "object") {
        const detailMessage = detail.message;
        if (typeof detailMessage === "string" && detailMessage.trim()) {
          message = detailMessage;
        }
        if (typeof detail.code === "string") {
          code = detail.code;
        }
      } else if (typeof json?.message === "string" && json.message.trim()) {
        message = json.message;
      }
    } catch {
      try {
        const bodyText = await res.text();
        if (bodyText?.trim()) {
          message = `${message}: ${bodyText}`;
        }
      } catch {
        // ignore
      }
    }

    const error = new Error(message) as ApiRequestError;
    error.code = code;
    error.status = res.status;
    throw error;
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

export function subscriptionPath(subscriptionId: SubscriptionId, suffix: string): string {
  return `/api/subscriptions/${encodeURIComponent(subscriptionId)}${suffix}`;
}

export async function fetchSubscriptions(): Promise<SubscriptionInfo[]> {
  return await apiJson<SubscriptionInfo[]>("/api/subscriptions");
}

export async function discoverSubscriptions(): Promise<DiscoverableSubscriptionInfo[]> {
  return await apiJson<DiscoverableSubscriptionInfo[]>("/api/subscriptions/discover");
}

export async function discoverSubscriptionResourceGroups(
  subscriptionId: SubscriptionId
): Promise<string[]> {
  return await apiJson<string[]>(
    subscriptionPath(subscriptionId, "/resource-groups/discover")
  );
}

export async function startSubscriptionMapping(
  subscriptionId: SubscriptionId,
  payload: SubscriptionMappingRequest
): Promise<SubscriptionMappingStatus> {
  return await apiJson<SubscriptionMappingStatus>(
    subscriptionPath(subscriptionId, "/map"),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload ?? {}),
    }
  );
}

export async function fetchSubscriptionMappingStatus(
  subscriptionId: SubscriptionId
): Promise<SubscriptionMappingStatus> {
  return await apiJson<SubscriptionMappingStatus>(
    subscriptionPath(subscriptionId, "/map/status")
  );
}

export async function uploadTerraformScripts(
  files: File[],
  subscriptionName: string,
  subscriptionId?: string
): Promise<TerraformUploadResponse> {
  const formData = new FormData();
  files.forEach(file => {
    formData.append("files", file);
  });

  formData.append("subscription_name", subscriptionName);
  if (subscriptionId) {
    formData.append("subscription_id", subscriptionId);
  }

  return await apiJson<TerraformUploadResponse>("/api/terraform/upload", {
    method: "POST",
    body: formData,
  });
}

export async function fetchWorkloadGraph(subscriptionId: SubscriptionId): Promise<RawGraphSnapshot> {
  return await apiJson<RawGraphSnapshot>(subscriptionPath(subscriptionId, `/graph`));
}

export async function fetchGroups(subscriptionId: SubscriptionId): Promise<NodeGroup[]> {
  return await apiJson<NodeGroup[]>(subscriptionPath(subscriptionId, `/groups`));
}

export async function listWorkloads(): Promise<WorkloadSummary[]> {
  return await apiJson<WorkloadSummary[]>("/api/workloads");
}

export async function getWorkload(workloadId: string): Promise<WorkloadRecord> {
  return await apiJson<WorkloadRecord>(`/api/workloads/${encodeURIComponent(workloadId)}`);
}

export async function createWorkload(payload: { name: string; view_state: WorkloadViewState }): Promise<WorkloadRecord> {
  return await apiJson<WorkloadRecord>("/api/workloads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function updateWorkload(
  workloadId: string,
  payload: { name?: string; view_state?: WorkloadViewState }
): Promise<WorkloadRecord> {
  return await apiJson<WorkloadRecord>(`/api/workloads/${encodeURIComponent(workloadId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteWorkload(workloadId: string): Promise<void> {
  await apiNoBody(`/api/workloads/${encodeURIComponent(workloadId)}`, { method: "DELETE" });
}

export async function acceptEdge(subscriptionId: SubscriptionId, edgeId: string): Promise<void> {
  await apiNoBody(subscriptionPath(subscriptionId, `/edges/${encodeURIComponent(edgeId)}/accept`), { method: "POST" });
}

export async function rejectEdge(subscriptionId: SubscriptionId, edgeId: string): Promise<void> {
  await apiNoBody(subscriptionPath(subscriptionId, `/edges/${encodeURIComponent(edgeId)}/reject`), { method: "POST" });
}

export async function deleteEdge(subscriptionId: SubscriptionId, edgeId: string): Promise<void> {
  await apiNoBody(subscriptionPath(subscriptionId, `/edges/${encodeURIComponent(edgeId)}`), { method: "DELETE" });
}

export async function reverseEdgeDirection(subscriptionId: SubscriptionId, edgeId: string): Promise<{ edge?: RawGraphEdge; old_edge_id?: string } & Record<string, unknown>> {
  return await apiJson(subscriptionPath(subscriptionId, `/edges/${encodeURIComponent(edgeId)}/reverse`), { method: "POST" });
}

export async function createManualEdge(
  subscriptionId: SubscriptionId,
  payload: { source: string; target: string; relationship: string }
): Promise<{ edge?: RawGraphEdge } & Record<string, unknown>> {
  return await apiJson(subscriptionPath(subscriptionId, "/edges"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function syncBridgeEdges(
  subscriptionId: SubscriptionId,
  edges: Array<{ source: string; target: string; relationship: string; confidence?: number }>
): Promise<{ count: number }> {
  return await apiJson(subscriptionPath(subscriptionId, "/bridge-edges"), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ edges }),
  });
}

export async function clearBridgeEdges(subscriptionId: SubscriptionId): Promise<{ status: string }> {
  return await apiJson(subscriptionPath(subscriptionId, "/bridge-edges"), {
    method: "DELETE",
  });
}


export async function patchNode(
  subscriptionId: SubscriptionId,
  nodeId: string,
  payload: {
    name?: string;
    layer?: number | null;
    color?: string | null;
    icon?: string | null;
    criticality_score?: number | null;
    hidden?: boolean | null;
  }
): Promise<any> {
  // URL-encode nodeId so slashes don't break the path, `:path` converter will decode it
  return await apiJson(`/api/subscriptions/${encodeURIComponent(subscriptionId)}/nodes/${encodeURIComponent(nodeId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function resetNode(subscriptionId: SubscriptionId, nodeId: string): Promise<void> {
  // URL-encode nodeId so slashes don't break the path, `:path` converter will decode it
  await apiNoBody(`/api/subscriptions/${encodeURIComponent(subscriptionId)}/nodes/${encodeURIComponent(nodeId)}`, { method: "DELETE" });
}

// Group API functions
export async function createGroup(
  subscriptionId: SubscriptionId,
  payload: { id: string; name: string; nodes: string[] }
): Promise<NodeGroup> {
  return await apiJson(`/api/subscriptions/${encodeURIComponent(subscriptionId)}/groups`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function updateGroup(
  subscriptionId: SubscriptionId,
  groupId: string,
  payload: { name?: string; nodes?: string[] }
): Promise<NodeGroup> {
  return await apiJson(`/api/subscriptions/${encodeURIComponent(subscriptionId)}/groups/${encodeURIComponent(groupId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteGroup(subscriptionId: SubscriptionId, groupId: string): Promise<void> {
  await apiNoBody(`/api/subscriptions/${encodeURIComponent(subscriptionId)}/groups/${encodeURIComponent(groupId)}`, {
    method: "DELETE",
  });
}

export async function addNodesToGroup(
  subscriptionId: SubscriptionId,
  groupId: string,
  nodeIds: string[]
): Promise<NodeGroup> {
  return await apiJson<NodeGroup>(`/api/subscriptions/${encodeURIComponent(subscriptionId)}/groups/${encodeURIComponent(groupId)}/nodes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ node_ids: nodeIds }),
  });
}

export async function removeNodeFromGroup(
  subscriptionId: SubscriptionId,
  groupId: string,
  nodeId: string
): Promise<NodeGroup | null> {
  const result = await apiJson<NodeGroup | { status: string; group_id: string }>(
    `/api/subscriptions/${encodeURIComponent(subscriptionId)}/groups/${encodeURIComponent(groupId)}/nodes/${encodeURIComponent(nodeId)}`,
    { method: "DELETE" }
  );
  
  // If group was deleted, return null
  if ('status' in result && result.status === 'deleted') {
    return null;
  }
  
  return result as NodeGroup;
}

export interface ResiliencyCheckMetrics {
  total_checks: number;
  passed_checks: number;
  failed_checks: number;
  pass_percentage: number;
}

export interface ResiliencySummary {
  [resourceId: string]: ResiliencyCheckMetrics;
}

export async function getResiliencySummary(
  subscriptionId: string
): Promise<ResiliencySummary> {
  return apiJson(`/api/resilience/evaluate/${subscriptionId}/summary`);
}
