import type { GraphEdge, GraphNode } from "../components/GraphCanvasReactflow";
import type {
  LlmEdgeSuggestion,
  LlmNodeAnnotation,
  LlmNodeAnnotationPayload,
  RawGraphSnapshot,
} from "../api/workloads";

export type ViewLevel = "overview" | "network" | "full";

export const LEVEL_TO_MAX_IMPORTANCE: Record<ViewLevel, number> = {
  overview: 1,
  network: 2,
  full: 3,
};

export interface AiTooltip {
  title: string;
  items: Array<{ label: string; value: string }>;
}

export interface GraphSnapshot {
  nodes: GraphNode[];
  edges: GraphEdge[];
  llm_annotations?: {
    nodes?: LlmNodeAnnotation[];
    edges?: LlmEdgeSuggestion[];
  };
}

export function normalizeTypeString(rawType?: string): string {
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
}

export function canonicalTypeForNode(node: GraphNode): string {
  const meta = node.metadata as any;
  const candidates = [
    node.type,
    meta?.raw_type,
    meta?.resource_type,
    meta?.provider,
    meta?.azure_type,
    meta?.kind,
    meta?.type,
  ];

  for (const c of candidates) {
    const normalized = normalizeTypeString(c);
    if (normalized !== "resource") return normalized;
  }

  const haystack = `${node.id} ${node.name} ${meta?.original_name ?? ""}`.toLowerCase();
  if (
    haystack.includes("/virtualmachines/") ||
    haystack.includes(" virtual machine") ||
    haystack.includes(" virtualmachine") ||
    haystack.includes(" vm")
  ) {
    return "vm";
  }

  return "resource";
}

export function extractResourceGroup(node: GraphNode): { key: string; label: string } | null {
  const meta = (node as any)?.metadata ?? {};
  const raw =
    meta.resource_group ??
    meta.resourceGroup ??
    meta.resource_group_name ??
    meta.resourceGroupName ??
    meta.resourcegroup;

  if (!raw) return null;
  const label = String(raw).trim();
  if (!label) return null;
  return { key: label.toLowerCase(), label };
}

export function renderStars(score: number): string {
  const full = Math.floor(score / 2);
  const half = score % 2 === 1;
  let stars = "★".repeat(full);
  if (half) stars += "⯪";
  stars += "☆".repeat(5 - full - (half ? 1 : 0));
  return stars;
}

export function getEffectiveCriticalityScore(
  ann: LlmNodeAnnotationPayload | undefined,
  overrides: Map<string, number>,
  nodeId: string
): number | undefined {
  const override = overrides.get(nodeId);
  if (override !== undefined) return override;
  return ann?.criticality_score;
}

export function buildAiTooltip(
  ann: LlmNodeAnnotationPayload | undefined,
  baseName: string | undefined
): AiTooltip | undefined {
  if (!ann) return undefined;

  const items: Array<{ label: string; value: string }> = [];
  if (ann.display_name) items.push({ label: "Name", value: ann.display_name });
  if (baseName) items.push({ label: "Original", value: baseName });
  if (ann.priority) items.push({ label: "Priority", value: ann.priority });
  if (ann.criticality_score !== undefined) items.push({ label: "Criticality", value: `${ann.criticality_score}/10` });
  if (ann.confidence !== undefined) items.push({ label: "Confidence", value: `${Math.round((ann.confidence ?? 0) * 100)}%` });
  if (ann.reason) items.push({ label: "Reason", value: ann.reason });

  if (items.length === 0) return undefined;
  return { title: "AI suggestion", items };
}

export function normalizeGraph(raw: RawGraphSnapshot): GraphSnapshot {
  const edges: GraphEdge[] = (raw?.edges ?? []).map(e => ({
    id: e.id,
    source: e.from_id,
    target: e.to_id,
    relationship: e.relationship,
    confidence: e.confidence,
    status: e.status ?? "proposed",
    origin: e.source ?? "arg",
  }));

  return {
    nodes: (raw?.nodes ?? []) as unknown as GraphNode[],
    edges,
    llm_annotations: raw?.llm_annotations,
  };
}

export interface ServiceOptionCategory {
  category: string;
  services: Array<{ key: string; label: string }>;
}

export function computeServiceOptions(
  snapshot: GraphSnapshot,
  viewLevel: ViewLevel,
  aiLayerEnabled: boolean,
  userLayerEnabled: boolean
): ServiceOptionCategory[] {
  const annMap = new Map<string, LlmNodeAnnotationPayload>();
  (snapshot.llm_annotations?.nodes ?? []).forEach(entry => {
    if (entry?.node_id) annMap.set(entry.node_id, entry.annotations || {});
  });

  const maxImportance = LEVEL_TO_MAX_IMPORTANCE[viewLevel];
  const categoryMap = new Map<string, Map<string, string>>();

  (snapshot.nodes || []).forEach(n => {
    const key = canonicalTypeForNode(n);
    if (!key) return;

    const ann = annMap.get(n.id);

    const baseImportance = (n.metadata as any)?.original_importance ?? (n.metadata as any)?.importance ?? 3;
    let importance = baseImportance;

    if (aiLayerEnabled && ann?.layer !== undefined) importance = ann.layer;
    if (userLayerEnabled && (n.metadata as any)?.override) {
      importance = (n.metadata as any)?.importance ?? importance;
    }

    if (importance > maxImportance) return;

    const category = ann?.azure_service_category || "Other";
    const label = ann?.azure_service_name || key;

    if (!categoryMap.has(category)) categoryMap.set(category, new Map());
    const servicesInCategory = categoryMap.get(category)!;
    if (!servicesInCategory.has(key)) servicesInCategory.set(key, label);
  });

  return Array.from(categoryMap.entries())
    .map(([category, servicesMap]) => ({
      category,
      services: Array.from(servicesMap.entries())
        .map(([key, label]) => ({ key, label }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    }))
    .sort((a, b) => a.category.localeCompare(b.category));
}

export function computeResourceGroupOptions(
  snapshot: GraphSnapshot,
  viewLevel: ViewLevel,
  aiLayerEnabled: boolean,
  userLayerEnabled: boolean
): Array<{ key: string; label: string }> {
  const annMap = new Map<string, LlmNodeAnnotationPayload>();
  (snapshot.llm_annotations?.nodes ?? []).forEach(entry => {
    if (entry?.node_id) annMap.set(entry.node_id, entry.annotations || {});
  });

  const maxImportance = LEVEL_TO_MAX_IMPORTANCE[viewLevel];
  const groups = new Map<string, string>();

  (snapshot.nodes || []).forEach(n => {
    const ann = annMap.get(n.id);

    const baseImportance = (n.metadata as any)?.original_importance ?? (n.metadata as any)?.importance ?? 3;
    let importance = baseImportance;

    if (aiLayerEnabled && ann?.layer !== undefined) importance = ann.layer;
    if (userLayerEnabled && (n.metadata as any)?.override) {
      importance = (n.metadata as any)?.importance ?? importance;
    }

    if (importance > maxImportance) return;

    const info = extractResourceGroup(n);
    if (!info) return;

    if (!groups.has(info.key)) groups.set(info.key, info.label);
  });

  return Array.from(groups.entries())
    .map(([key, label]) => ({ key, label }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

export interface ViewGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  annotationMap: Map<string, LlmNodeAnnotationPayload>;
}

export function buildViewGraph(args: {
  snapshot: GraphSnapshot;
  aiLayerEnabled: boolean;
  userLayerEnabled: boolean;
  serviceFilter: Set<string>;
  resourceGroupFilter: Set<string>;
  criticalityOverrides: Map<string, number>;
}): ViewGraph {
  const { snapshot, aiLayerEnabled, userLayerEnabled, serviceFilter, resourceGroupFilter, criticalityOverrides } = args;

  const annotationMap = new Map<string, LlmNodeAnnotationPayload>();
  if (aiLayerEnabled) {
    (snapshot.llm_annotations?.nodes ?? []).forEach(entry => {
      if (entry?.node_id) annotationMap.set(entry.node_id, entry.annotations || {});
    });
  }

  const visibleNodes: GraphNode[] = (snapshot.nodes || [])
    .map(n => {
      const ann = annotationMap.get(n.id);
      const normalizedType = canonicalTypeForNode(n);

      const baseName = (n.metadata as any)?.original_name ?? n.name;
      const baseImportance = (n.metadata as any)?.original_importance ?? (n.metadata as any)?.importance ?? 3;

      let name = baseName;
      let importance = baseImportance;

      if (aiLayerEnabled) {
        if (ann?.display_name) name = ann.display_name;
        if (ann?.layer !== undefined) importance = ann.layer;
      }

      if (userLayerEnabled && (n.metadata as any)?.override) {
        name = n.name ?? name;
        importance = (n.metadata as any)?.importance ?? importance;
      }

      const effectiveCriticality = getEffectiveCriticalityScore(ann, criticalityOverrides, n.id);

      return {
        ...n,
        type: normalizedType,
        name,
        metadata: {
          ...(n.metadata as any),
          importance,
          ai_annotation: ann,
          original_name: baseName,
          raw_type: n.type,
          ai_tooltip: aiLayerEnabled ? buildAiTooltip(ann, baseName) : undefined,
          criticality_score: effectiveCriticality,
          criticality_stars: renderStars(effectiveCriticality ?? 5),
        } as Record<string, unknown>,
      };
    })
    .filter(n => {
      const typeAllowed = serviceFilter.size === 0 || serviceFilter.has((n as any).type);
      const rgInfo = extractResourceGroup(n);
      const groupAllowed = resourceGroupFilter.size === 0 || !rgInfo || resourceGroupFilter.has(rgInfo.key);
      return typeAllowed && groupAllowed;
    });

  const visibleIds = new Set(visibleNodes.map(n => n.id));

  const baseEdges = snapshot.edges
    .filter(e => !(!userLayerEnabled && e.origin === "manual"))
    .filter(e => visibleIds.has(e.source) && visibleIds.has(e.target));

  const existingKeys = new Set(baseEdges.map(e => `${e.source}|${e.relationship}|${e.target}`));

  const suggestedEdges: GraphEdge[] = aiLayerEnabled
    ? (snapshot.llm_annotations?.edges ?? [])
        .map(s => ({
          id: `llm-${s.from_id}-${s.relationship}-${s.to_id}`,
          source: s.from_id,
          target: s.to_id,
          relationship: s.relationship,
          confidence: s.confidence ?? 0.5,
          status: "proposed" as const,
          origin: s.source ?? "llm",
        }))
        .filter(e => !existingKeys.has(`${e.source}|${e.relationship}|${e.target}`) && visibleIds.has(e.source) && visibleIds.has(e.target))
    : [];

  return {
    nodes: visibleNodes,
    edges: [...baseEdges, ...suggestedEdges],
    annotationMap,
  };
}
