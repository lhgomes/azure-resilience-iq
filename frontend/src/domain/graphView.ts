import type { GraphEdge, GraphNode } from "../components/GraphCanvasReactflow";
import type {
  LlmEdgeSuggestion,
  LlmNodeAnnotation,
  LlmNodeAnnotationPayload,
  RawGraphSnapshot,
  NodeGroup,
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
  node_overrides?: Record<string, Record<string, unknown>>;
  edge_overrides?: Record<string, Record<string, unknown>>;
  groups?: NodeGroup[];
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

  const haystack = `${node.id} ${node.name}`.toLowerCase();
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

function buildAnnotationMap(
  snapshot: GraphSnapshot
): Map<string, LlmNodeAnnotationPayload> {
  const annMap = new Map<string, LlmNodeAnnotationPayload>();
  (snapshot.llm_annotations?.nodes ?? []).forEach(entry => {
    if (entry?.node_id) annMap.set(entry.node_id, entry.annotations || {});
  });
  return annMap;
}

function computeEffectiveImportance(
  node: GraphNode,
  ann: LlmNodeAnnotationPayload | undefined,
  nodeOverride: Record<string, unknown> | undefined,
  aiLayerEnabled: boolean,
  userLayerEnabled: boolean
): number {
  const baseImportance = (node.metadata as any)?.importance ?? 3;
  let importance = baseImportance;

  if (aiLayerEnabled && ann?.layer !== undefined) importance = ann.layer;
  if (userLayerEnabled && typeof (nodeOverride as any)?.layer === "number")
    importance = (nodeOverride as any).layer as number;

  return importance;
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

function formatLayer(layer: number): string {
  if (layer === 1) return "L1";
  if (layer === 2) return "L2";
  if (layer === 3) return "L3";
  return String(layer);
}

export function buildUserTooltip(args: {
  originalName: string | undefined;
  nameOverride: string | undefined;
  originalImportance: number | undefined;
  layerOverride: number | undefined;
  colorOverride: string | undefined;
  iconOverride: string | undefined;
  criticalityOverride: number | undefined;
  aiCriticality: number | undefined;
}): AiTooltip | undefined {
  const {
    originalName,
    nameOverride,
    originalImportance,
    layerOverride,
    colorOverride,
    iconOverride,
    criticalityOverride,
    aiCriticality,
  } = args;

  const items: Array<{ label: string; value: string }> = [];

  if (nameOverride && originalName && nameOverride !== originalName) {
    items.push({ label: "Name", value: `${nameOverride} (was ${originalName})` });
  }

  if (layerOverride !== undefined && layerOverride !== null) {
    const suffix =
      typeof originalImportance === "number" && originalImportance !== layerOverride
        ? ` (was ${formatLayer(originalImportance)})`
        : "";
    items.push({ label: "Layer", value: `${formatLayer(layerOverride)} ${suffix}` });
  }

  if (colorOverride) {
    items.push({ label: "Color", value: colorOverride });
  }

  if (iconOverride) {
    const file = iconOverride.split("/").pop() || iconOverride;
    items.push({ label: "Icon", value: file });
  }

  if (criticalityOverride !== undefined) {
    // Only show as a user change if it differs from AI suggestion (when available).
    if (aiCriticality === undefined || criticalityOverride !== aiCriticality) {
      const suffix = aiCriticality !== undefined ? ` (AI ${aiCriticality}/10)` : "";
      items.push({ label: "Criticality", value: `${criticalityOverride}/10${suffix}` });
    }
  }

  if (items.length === 0) return undefined;
  return { title: "User input", items };
}

export function normalizeGraph(raw: RawGraphSnapshot): GraphSnapshot {
  const edges: GraphEdge[] = (raw?.edges ?? []).map(e => ({
    id: e.id,
    source: e.from_id,
    target: e.to_id,
    relationship: e.relationship,
    confidence: e.confidence,
    status: e.status ?? "proposed",
    evidence: e.evidence,
    origin: e.source ?? "arg",
  }));

  return {
    nodes: (raw?.nodes ?? []) as unknown as GraphNode[],
    edges,
    llm_annotations: raw?.llm_annotations,
    node_overrides: raw?.node_overrides,
    edge_overrides: raw?.edge_overrides,
    groups: raw?.groups ?? [],
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
  const annMap = buildAnnotationMap(snapshot);
  const maxImportance = LEVEL_TO_MAX_IMPORTANCE[viewLevel];
  const categoryMap = new Map<string, Map<string, string>>();

  (snapshot.nodes || []).forEach(n => {
    const key = canonicalTypeForNode(n);
    if (!key) return;

    const ann = annMap.get(n.id);
    const nodeOverride = userLayerEnabled ? (snapshot.node_overrides ?? {})[n.id] : undefined;
    const importance = computeEffectiveImportance(n, ann, nodeOverride, aiLayerEnabled, userLayerEnabled);

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
  const annMap = buildAnnotationMap(snapshot);
  const maxImportance = LEVEL_TO_MAX_IMPORTANCE[viewLevel];
  const groups = new Map<string, string>();

  (snapshot.nodes || []).forEach(n => {
    const ann = annMap.get(n.id);
    const nodeOverride = userLayerEnabled ? (snapshot.node_overrides ?? {})[n.id] : undefined;
    const importance = computeEffectiveImportance(n, ann, nodeOverride, aiLayerEnabled, userLayerEnabled);

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
}): ViewGraph {
  const { snapshot, aiLayerEnabled, userLayerEnabled, serviceFilter, resourceGroupFilter } = args;
  const annotationMap = aiLayerEnabled ? buildAnnotationMap(snapshot) : new Map();

  // Build a map from node_id to group info
  const nodeToGroupMap = new Map<string, { groupId: string; groupLabel: string }>();
  if (snapshot.groups) {
    for (const group of snapshot.groups) {
      for (const nodeId of group.nodes) {
        nodeToGroupMap.set(nodeId, { groupId: group.id, groupLabel: group.name });
      }
    }
  }

  const visibleNodes: GraphNode[] = (snapshot.nodes || [])
    .map(n => {
      const ann = annotationMap.get(n.id);
      const nodeOverride = userLayerEnabled ? (snapshot.node_overrides ?? {})[n.id] : undefined;
      const normalizedType = canonicalTypeForNode(n);

      const meta = (n.metadata as any) ?? {};
      
      // Extract group info from the groups array instead of nodeOverride
      const groupInfo = nodeToGroupMap.get(n.id);
      const groupId = groupInfo?.groupId;
      const groupLabel = groupInfo?.groupLabel;
      
      const baseName = n.name;

      let name = baseName;
      let importance = computeEffectiveImportance(n, ann, nodeOverride, aiLayerEnabled, userLayerEnabled);
      let color = meta.color as string | undefined;
      let icon = meta.icon as string | undefined;

      if (aiLayerEnabled && ann?.display_name) name = ann.display_name;
      if (userLayerEnabled && typeof (nodeOverride as any)?.name === "string")
        name = (nodeOverride as any).name as string;

      if (userLayerEnabled && typeof (nodeOverride as any)?.color === "string")
        color = (nodeOverride as any).color as string;
      if (userLayerEnabled && typeof (nodeOverride as any)?.icon === "string")
        icon = (nodeOverride as any).icon as string;

      const criticalityOverride = userLayerEnabled ? (nodeOverride as any)?.criticality_score : undefined;
      const effectiveCriticality =
        typeof criticalityOverride === "number"
          ? criticalityOverride
          : aiLayerEnabled && typeof ann?.criticality_score === "number"
            ? ann.criticality_score
            : undefined;

      const baseImportance = meta.importance ?? 3;
      const user_tooltip = buildUserTooltip({
        originalName: baseName,
        nameOverride: typeof (nodeOverride as any)?.name === "string" ? (nodeOverride as any).name : undefined,
        originalImportance: baseImportance,
        layerOverride: typeof (nodeOverride as any)?.layer === "number" ? (nodeOverride as any).layer : undefined,
        colorOverride: typeof (nodeOverride as any)?.color === "string" ? (nodeOverride as any).color : undefined,
        iconOverride: typeof (nodeOverride as any)?.icon === "string" ? (nodeOverride as any).icon : undefined,
        criticalityOverride: typeof criticalityOverride === "number" ? criticalityOverride : undefined,
        aiCriticality: ann?.criticality_score,
      });

      return {
        ...n,
        type: normalizedType,
        name,
        metadata: {
          ...meta,
          importance,
          color,
          icon,
          ai_annotation: aiLayerEnabled ? ann : undefined,
          raw_type: n.type,
          ai_tooltip: aiLayerEnabled ? buildAiTooltip(ann, baseName) : undefined,
          user_tooltip,
          criticality_score: effectiveCriticality,
          criticality_stars: renderStars(effectiveCriticality ?? 5),
          user_override: nodeOverride,
          group_id: groupId,
          group_label: groupLabel,
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
