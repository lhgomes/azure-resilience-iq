import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ReactFlowProvider } from "reactflow";
import GraphCanvas, {
  GraphNode,
  GraphEdge,
  GraphCanvasHandle
} from "../components/GraphCanvasReactflow";
import ResiliencySummary from "../components/ResiliencySummary";
import ZonalResiliencySummary from "../components/ZonalResiliencySummary";
import TabbedView from "../components/TabbedView";
import EdgeDrawer, {
  EdgeData
} from "../components/EdgeDrawer";
import NodeDrawer, { NodeData } from "../components/NodeDrawer";

import WorkloadSidebar from "../components/WorkloadSidebar";
import LegendPanel from "../components/LegendPanel";
import { ChatPanel } from "../components/Chat";
import "../components/common/spin.css";
import { LLMChatService } from "../services/chatService";
import {
  acceptEdge,
  createManualEdge,
  clearBridgeEdges,
  deleteEdge,
  reverseEdgeDirection,
  fetchWorkloadGraph,
  fetchSubscriptions,
  discoverSubscriptions,
  discoverSubscriptionResourceGroups,
  startSubscriptionMapping,
  fetchSubscriptionMappingStatus,
  uploadTerraformScripts,
  patchNode,
  rejectEdge,
  resetNode,
  createGroup,
  updateGroup,
  deleteGroup,
  addNodesToGroup,
  removeNodeFromGroup,
  applyServiceGroup,
  applyServiceGroupFromWorkload,
  exportServiceGroup,
  listAzureServiceGroups,
  getServiceGroupAvailability,
  fetchServiceGroupMembers,
  deleteServiceGroup,
  listWorkloads,
  getWorkload,
  createWorkload,
  updateWorkload,
  deleteWorkload,
  type ServiceGroupArtifact,
  type ServiceGroupBinding,
  type ServiceGroupFormat,
  type ServiceGroupImportProgress,
  type ServiceGroupSummary,
  type SubscriptionInfo,
  type DiscoverableSubscriptionInfo,
  type SubscriptionMappingStatus,
  type WorkloadRecord,
  type WorkloadSummary,
  type WorkloadViewState,
} from "../api/workloads";
import {
  buildViewGraph,
  canonicalTypeForNode,
  canonicalTypeForResourceId,
  computeResourceGroupOptions,
  computeServiceOptions,
  computeValidationSourceOptions,
  LEVEL_TO_MAX_IMPORTANCE,
  normalizeGraph,
  normalizeTypeString,
  type GraphSnapshot,
  type ViewLevel,
} from "../domain/graphView";
import { calculateResiliencyScore, getElementWeight, DEFAULT_WEIGHTS, type ResiliencyWeights } from "../utils/resilienceScore";
import { getZonalResiliency, type ZonalResiliencyResponse } from "../api/resilience";
import { mergeGraphSnapshots, mergeResiliencyEvaluations, mergeZonalResiliencyData } from "../utils/multiSubscriptionMerge";
import { ArrowCollapseAll16Regular, ArrowExpandAll16Regular, ArrowSync16Regular, Dismiss12Regular, DocumentPdf20Regular } from "@fluentui/react-icons";

interface ResiliencyPdfReport {
  score: number;
  totalChecks: number;
  passedChecks: number;
  failedChecks: number;
  categories: Array<{ name: string; score: number; passed: number; failed: number }>;
  impacts: Array<{ name: string; score: number; passed: number; failed: number }>;
  services: Array<{ name: string; score: number; passed: number; failed: number }>;
  recommendations: Array<{
    title: string;
    benefit: string;
    category: string;
    impact: string;
    resources: string[];
    contributionPercent: number;
  }>;
}

// Subscription-aware view: user selects one or more subscriptions

const WorkloadView: React.FC = () => {
  const [subscriptions, setSubscriptions] = useState<SubscriptionInfo[]>([]);
  const [selectedSubscriptions, setSelectedSubscriptions] = useState<Set<string>>(new Set());
  const selectedSubscriptionIds = useMemo(
    () => Array.from(selectedSubscriptions).sort(),
    [selectedSubscriptions]
  );
  const selectedSubscriptionOptions = useMemo(
    () =>
      subscriptions
        .filter(sub => selectedSubscriptions.has(sub.id))
        .map(sub => ({ id: sub.id, name: sub.name, resource_count: sub.resource_count })),
    [subscriptions, selectedSubscriptions]
  );
  const selectionKey = useMemo(
    () => selectedSubscriptionIds.join("|"),
    [selectedSubscriptionIds]
  );
  const singleSubscriptionId = useMemo(
    () => (selectedSubscriptionIds.length === 1 ? selectedSubscriptionIds[0] : null),
    [selectedSubscriptionIds]
  );
  const storageKey = useMemo(
    () => (selectionKey ? `workload_graph_${selectionKey}` : "workload_graph_none"),
    [selectionKey]
  );
  const [graph, setGraph] = useState<GraphSnapshot | null>(null);
  const [resilience_evaluations, setResiliencyEvaluations] = useState<Record<string, any> | null>(null);
  const [resilience_overrides, setResiliencyOverrides] = useState<Record<string, any>>({});
  const [resilience_data, setResiliencyData] = useState<any | null>(null);
  const [zonal_resilience_data, setZonalResiliencyData] = useState<ZonalResiliencyResponse | null>(null);
  const [zonal_resilience_loading, setZonalResiliencyLoading] = useState(false);
  const [zonal_resilience_error, setZonalResiliencyError] = useState<string | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<EdgeData | null>(null);
  const [selectedNode, setSelectedNode] = useState<NodeData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewLevel, setViewLevel] = useState<ViewLevel>("overview");
  const [showLegend, setShowLegend] = useState(false);
  const hiddenResourcesCount = useMemo(() => {
    if (!graph?.node_overrides) return 0;
    return Object.values(graph.node_overrides).filter((override: any) => override?.hidden === true).length;
  }, [graph]);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState(320);
  const [isResizing, setIsResizing] = useState(false);
  const [activeSubscriptionId, setActiveSubscriptionId] = useState<string | null>(null);

  const [workloads, setWorkloads] = useState<WorkloadSummary[]>([]);
  const [activeWorkloadId, setActiveWorkloadId] = useState<string | null>(null);
  const [activeWorkloadState, setActiveWorkloadState] = useState<WorkloadViewState | null>(null);
  const [workloadName, setWorkloadName] = useState("");
  const [regionAzCounts, setRegionAzCounts] = useState<Record<string, 1 | 2 | 3>>({});
  const [workloadError, setWorkloadError] = useState<string | null>(null);
  const chatSubscriptionId = useMemo(() => {
    if (singleSubscriptionId) return singleSubscriptionId;
    if (selectedSubscriptionIds.length === 0) return "";
    if (activeWorkloadId) return selectedSubscriptionIds[0];

    const selected = subscriptions.filter(sub => selectedSubscriptions.has(sub.id));
    if (selected.length === 0) return selectedSubscriptionIds[0];

    const ranked = [...selected].sort((a, b) => {
      const countA = a.resource_count ?? 0;
      const countB = b.resource_count ?? 0;
      if (countA !== countB) return countB - countA;
      return a.id.localeCompare(b.id);
    });
    return ranked[0]?.id || selectedSubscriptionIds[0];
  }, [singleSubscriptionId, selectedSubscriptionIds, activeWorkloadId, subscriptions, selectedSubscriptions]);

  // Default both layers to enabled; no URL sync
  const [aiLayerEnabled, setAiLayerEnabled] = useState(true);
  const [userLayerEnabled, setUserLayerEnabled] = useState(true);
  const [serviceFilter, setServiceFilter] = useState<Set<string>>(new Set());
  const [resourceGroupFilter, setResourceGroupFilter] = useState<Set<string>>(new Set());
  const [validationSourceFilter, setValidationSourceFilter] = useState<Set<string>>(new Set());
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());

  const serviceFilterUserTouchedRef = React.useRef(false);
  const resourceGroupFilterUserTouchedRef = React.useRef(false);
  const validationSourceFilterUserTouchedRef = React.useRef(false);

  const [groupToolbarSelection, setGroupToolbarSelection] = useState<{
    selectedNodeIds: string[];
    selectedGroupId: string | null;
    selectedGroupLabel?: string;
    selectedGroupMemberIds?: string[];
  }>({ selectedNodeIds: [], selectedGroupId: null });
  const [groupToolbarName, setGroupToolbarName] = useState<string>("");
  const [graphViewRevision, setGraphViewRevision] = useState(0);

  // Azure Service Group apply/export state (scoped to the selected group)
  const [serviceGroupFormat, setServiceGroupFormat] = useState<ServiceGroupFormat>("terraform");
  const [serviceGroupBusy, setServiceGroupBusy] = useState<boolean>(false);
  // Separate from serviceGroupBusy: a Service Group deletion is in flight. Kept
  // distinct so a delete drives the status toast without spinning the Save
  // button (which reflects save/sync, not delete).
  const [serviceGroupDeleting, setServiceGroupDeleting] = useState<boolean>(false);
  const [serviceGroupMessage, setServiceGroupMessage] = useState<
    { tone: "info" | "success" | "error" | "warn"; text: string } | null
  >(null);

  // Auto-dismiss the Service Group status toast 10s after an outcome is shown.
  // Skipped while busy so the "syncing…" indicator stays until the op finishes.
  useEffect(() => {
    if (!serviceGroupMessage || serviceGroupBusy || serviceGroupDeleting) return;
    const timer = window.setTimeout(() => setServiceGroupMessage(null), 10000);
    return () => window.clearTimeout(timer);
  }, [serviceGroupMessage, serviceGroupBusy, serviceGroupDeleting]);

  // Authoritative membership of an imported Azure Service Group (SG -> Workload).
  const [serviceGroupBinding, setServiceGroupBinding] = useState<ServiceGroupBinding | null>(null);

  // Pending "create a Service Group?" confirmation raised from Save Workload when
  // the workload is not yet bound to a Service Group.
  const [pendingServiceGroupCreate, setPendingServiceGroupCreate] = useState<
    { memberIds: string[] } | null
  >(null);
  // Pending "delete the Service Group too?" confirmation raised from Delete
  // Workload when the workload is bound to an Azure Service Group.
  const [pendingWorkloadDelete, setPendingWorkloadDelete] = useState<
    { workloadId: string; workloadName: string; serviceGroupName: string; serviceGroupDisplayName: string } | null
  >(null);
  // Parent choice for a NEW Service Group: "" = tenant root, otherwise an
  // existing Service Group id. Only used by the create confirmation modal.
  const [parentServiceGroupOptions, setParentServiceGroupOptions] = useState<ServiceGroupSummary[]>([]);
  const [selectedParentServiceGroupId, setSelectedParentServiceGroupId] = useState<string>("");

  // Progress surface for the SG -> Workload import (create view + map member subscriptions).
  const [serviceGroupImport, setServiceGroupImport] = useState<ServiceGroupImportProgress | null>(null);
  const [groupCreateRequest, setGroupCreateRequest] = useState<{ nonce: number; label: string } | null>(null);

  // Whether the backend identity can read Service Groups at tenant scope. When
  // false the entire Service Group integration is disabled: read requires a
  // tenant-root grant a standard deploy principal cannot self-assign. Probed once.
  const [serviceGroupAvailable, setServiceGroupAvailable] = useState<boolean>(true);
  const [serviceGroupUnavailableReason, setServiceGroupUnavailableReason] = useState<string | null>(null);

  const normalizeRegionKey = useCallback((region: string): string => {
    return String(region || "").trim().toLowerCase().replace(/\s+/g, "");
  }, []);

  useEffect(() => {
    let cancelled = false;
    getServiceGroupAvailability()
      .then(res => {
        if (cancelled) return;
        setServiceGroupAvailable(res.available);
        setServiceGroupUnavailableReason(
          res.available ? null : res.reason ?? "Service Group integration is unavailable for the backend identity."
        );
      })
      .catch(() => {
        if (cancelled) return;
        // Fail safe: if we can't verify access, disable SG actions rather than offer broken ones.
        setServiceGroupAvailable(false);
        setServiceGroupUnavailableReason(
          "Could not verify Service Group availability. The backend identity may lack access or Azure is unreachable."
        );
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Track if user has made changes requiring refresh
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Chat availability state
  const [isChatAvailable, setIsChatAvailable] = useState(true); // Default to true, check on mount
  const [chatAvailabilityChecked, setChatAvailabilityChecked] = useState(false);
  const [pendingRefreshSubscriptions, setPendingRefreshSubscriptions] = useState<Set<string>>(new Set());
  const [chatRefreshToken, setChatRefreshToken] = useState(0);
  const [availableSubscriptionsForMapping, setAvailableSubscriptionsForMapping] = useState<DiscoverableSubscriptionInfo[]>([]);
  const [mappingInProgress, setMappingInProgress] = useState(false);
  const [mappingStatus, setMappingStatus] = useState<SubscriptionMappingStatus | null>(null);
  const [mappingError, setMappingError] = useState<string | null>(null);
  const [mappingAuthRequired, setMappingAuthRequired] = useState(false);

  // Weights for resilience score calculation
  const [resilienceWeights, setResiliencyWeights] = useState<ResiliencyWeights>(DEFAULT_WEIGHTS);

  const lastSuggestedGroupNameRef = useRef<string>("");
  const lastGroupToolbarSelectionRef = useRef(groupToolbarSelection);
  const graphCanvasRef = useRef<GraphCanvasHandle>(null);
  const graphCaptureRef = useRef<HTMLDivElement>(null);
  const skipFilterResetRef = useRef(false);
  const pendingWorkloadApplyRef = useRef(false);
  const suppressNextNodeDrawerOpenRef = useRef(false);
  const [pendingGraphView, setPendingGraphView] = useState<WorkloadViewState["graph_view"] | null>(null);
  const skipNextFitViewRef = useRef(false);
  const [activeTabIndex, setActiveTabIndex] = useState(0);
  const [isExportingPdf, setIsExportingPdf] = useState(false);
  const [selectedRecommendationFocus, setSelectedRecommendationFocus] = useState<{ id?: string; title?: string } | null>(null);

  useEffect(() => {
    if (activeTabIndex !== 1) return;
    setSelectedNode(null);
    setSelectedEdge(null);
  }, [activeTabIndex]);

  const pendingRefreshCount = useMemo(
    () => pendingRefreshSubscriptions.size,
    [pendingRefreshSubscriptions]
  );

  const handleRecommendationSelect = useCallback((recommendationId: string, recommendationTitle?: string) => {
    if (!recommendationId && !recommendationTitle) return;
    setSelectedRecommendationFocus({
      id: recommendationId || undefined,
      title: recommendationTitle || undefined,
    });
    setActiveTabIndex(1);
  }, []);

  const handleExportPdf = useCallback(async (report: ResiliencyPdfReport) => {
    const previousTab = activeTabIndex;
    setActiveTabIndex(0);

    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    await graphCanvasRef.current?.fitView();
    await new Promise(resolve => window.setTimeout(resolve, 100));

    const graphElement = graphCaptureRef.current;
    if (!graphElement) {
      setActiveTabIndex(previousTab);
      throw new Error("The workload diagram is not available for capture.");
    }

    try {
      const [{ default: html2canvas }, { jsPDF }] = await Promise.all([
        import("html2canvas"),
        import("jspdf"),
      ]);
      const diagramCanvas = await html2canvas(graphElement, {
        backgroundColor: "#ffffff",
        scale: 1.5,
        useCORS: true,
        logging: false,
      });

      const pdf = new jsPDF({ orientation: "portrait", unit: "mm", format: "a4" });
      const pageWidth = pdf.internal.pageSize.getWidth();
      const margin = 14;
      const contentWidth = pageWidth - margin * 2;
      const scorePercent = Math.round(report.score * 100);
      const scoreColor: [number, number, number] = scorePercent >= 80
        ? [16, 185, 129]
        : scorePercent >= 60
          ? [245, 158, 11]
          : [220, 38, 38];

      pdf.setFont("helvetica", "bold");
      pdf.setFontSize(20);
      pdf.setTextColor(17, 24, 39);
      pdf.text("Azure Resiliency IQ", margin, 18);
      pdf.setFont("helvetica", "normal");
      pdf.setFontSize(10);
      pdf.setTextColor(107, 114, 128);
      pdf.text(`Evaluation report | ${new Date().toLocaleString()}`, margin, 25);

      const summaryCards = [
        { label: "Overall score", value: `${scorePercent}%`, color: scoreColor },
        { label: "Total resiliency items", value: String(report.totalChecks), color: [59, 130, 246] as [number, number, number] },
        { label: "Passed", value: String(report.passedChecks), color: [16, 185, 129] as [number, number, number] },
        { label: "Failed", value: String(report.failedChecks), color: [239, 68, 68] as [number, number, number] },
      ];
      const summaryGap = 3;
      const summaryWidth = (contentWidth - summaryGap * 3) / 4;
      summaryCards.forEach((card, index) => {
        const x = margin + index * (summaryWidth + summaryGap);
        pdf.setFillColor(249, 250, 251);
        pdf.roundedRect(x, 32, summaryWidth, 25, 2, 2, "F");
        pdf.setDrawColor(...card.color);
        pdf.setLineWidth(1);
        pdf.line(x, 34, x, 55);
        pdf.setFont("helvetica", "normal");
        pdf.setFontSize(8);
        pdf.setTextColor(75, 85, 99);
        pdf.text(card.label, x + 4, 40);
        pdf.setFont("helvetica", "bold");
        pdf.setFontSize(14);
        pdf.setTextColor(...card.color);
        pdf.text(card.value, x + 4, 51);
      });

      const drawBreakdown = (
        title: string,
        items: Array<{ name: string; score: number; passed: number; failed: number }>,
        startY: number,
      ): number => {
        pdf.setTextColor(31, 41, 55);
        pdf.setFont("helvetica", "bold");
        pdf.setFontSize(11);
        pdf.text(title, margin, startY);
        const gap = 3;
        const columns = 4;
        const cardWidth = (contentWidth - gap * (columns - 1)) / columns;
        const cardHeight = 18;
        items.forEach((item, index) => {
          const row = Math.floor(index / columns);
          const column = index % columns;
          const x = margin + column * (cardWidth + gap);
          const y = startY + 4 + row * (cardHeight + gap);
          pdf.setFillColor(249, 250, 251);
          pdf.roundedRect(x, y, cardWidth, cardHeight, 2, 2, "F");
          pdf.setFont("helvetica", "bold");
          pdf.setFontSize(7);
          pdf.setTextColor(31, 41, 55);
          const name = pdf.splitTextToSize(item.name, cardWidth - 17)[0] || item.name;
          pdf.text(name, x + 3, y + 5);
          pdf.setFontSize(10);
          pdf.setTextColor(item.score >= 0.8 ? 16 : item.score >= 0.6 ? 245 : 220, item.score >= 0.8 ? 185 : item.score >= 0.6 ? 158 : 38, item.score >= 0.8 ? 129 : item.score >= 0.6 ? 11 : 38);
          pdf.text(`${Math.round(item.score * 100)}%`, x + cardWidth - 3, y + 6, { align: "right" });
          pdf.setFont("helvetica", "normal");
          pdf.setFontSize(6);
          pdf.setTextColor(107, 114, 128);
          pdf.text("(weighted score)", x + 3, y + 8.5);
          pdf.setFontSize(6.5);
          pdf.text(`Checks: ${item.passed} passed | ${item.failed} failed`, x + 3, y + 14);
        });
        return startY + 7 + Math.ceil(items.length / columns) * (cardHeight + gap);
      };

      let overviewY = drawBreakdown("By resiliency category", report.categories, 67);
      overviewY = drawBreakdown("By impact level", report.impacts, overviewY);
      drawBreakdown("By Azure service", report.services, overviewY);

      pdf.addPage();
      pdf.setTextColor(31, 41, 55);
      pdf.setFont("helvetica", "bold");
      pdf.setFontSize(17);
      pdf.text("Workload diagram", margin, 20);
      pdf.setFont("helvetica", "normal");
      pdf.setFontSize(10);
      pdf.setTextColor(107, 114, 128);
      pdf.text("Resources and dependencies included in this evaluation", margin, 27);
      const imageData = diagramCanvas.toDataURL("image/png");
      const diagramY = 34;
      const availableDiagramHeight = pdf.internal.pageSize.getHeight() - diagramY - 12;
      const imageHeight = Math.min(availableDiagramHeight, (diagramCanvas.height * contentWidth) / diagramCanvas.width);
      const imageWidth = Math.min(contentWidth, (diagramCanvas.width * imageHeight) / diagramCanvas.height);
      pdf.addImage(imageData, "PNG", margin + (contentWidth - imageWidth) / 2, diagramY, imageWidth, imageHeight, undefined, "FAST");

      pdf.addPage();
      pdf.setTextColor(17, 24, 39);
      pdf.setFont("helvetica", "bold");
      pdf.setFontSize(17);
      pdf.text("Top recommendations", margin, 20);
      pdf.setFont("helvetica", "normal");
      pdf.setFontSize(10);
      pdf.setTextColor(107, 114, 128);
      pdf.text("Prioritized by contribution to workload risk", margin, 27);

      const recommendationColumns = {
        recommendation: { x: margin, width: 66 },
        benefit: { x: margin + 68, width: 42 },
        category: { x: margin + 112, width: 34 },
        impact: { x: margin + 148, width: 16 },
        weight: { x: margin + 166, width: 12 },
      };
      const drawRecommendationHeader = (headerY: number) => {
        pdf.setFillColor(243, 244, 246);
        pdf.rect(margin, headerY, contentWidth, 10, "F");
        pdf.setTextColor(55, 65, 81);
        pdf.setFont("helvetica", "bold");
        pdf.setFontSize(8);
        pdf.text("Recommendation / resources", recommendationColumns.recommendation.x + 2, headerY + 6);
        pdf.text("Benefit", recommendationColumns.benefit.x + 2, headerY + 6);
        pdf.text("Category", recommendationColumns.category.x + 2, headerY + 6);
        pdf.text("Impact", recommendationColumns.impact.x + 2, headerY + 6);
        pdf.text("Weight", recommendationColumns.weight.x + 2, headerY + 6);
      };

      let y = 38;
      drawRecommendationHeader(y);
      y += 12;
      if (report.recommendations.length === 0) {
        pdf.setTextColor(75, 85, 99);
        pdf.text("No failing recommendations were found.", margin, y);
      } else {
        report.recommendations.forEach((recommendation, index) => {
          const titleLines = pdf.splitTextToSize(recommendation.title, recommendationColumns.recommendation.width - 6) as string[];
          const resourceText = recommendation.resources.length > 0 ? recommendation.resources.join(", ") : "No resource name available";
          const resourceLines = pdf.splitTextToSize(resourceText, recommendationColumns.recommendation.width - 6) as string[];
          const benefitLines = pdf.splitTextToSize(recommendation.benefit, recommendationColumns.benefit.width - 6) as string[];
          const categoryLines = pdf.splitTextToSize(recommendation.category, recommendationColumns.category.width - 6) as string[];
          const blockHeight = Math.max(20, 8 + (titleLines.length + resourceLines.length) * 4, 8 + benefitLines.length * 4, 8 + categoryLines.length * 4);
          if (y + blockHeight > pdf.internal.pageSize.getHeight() - 12) {
            pdf.addPage();
            y = 16;
            drawRecommendationHeader(y);
            y += 12;
          }
          if (index % 2 === 0) {
            pdf.setFillColor(249, 250, 251);
            pdf.rect(margin, y, contentWidth, blockHeight, "F");
          }
          const drawInCell = (column: { x: number; width: number }, draw: () => void) => {
            pdf.saveGraphicsState();
            pdf.rect(column.x, y, column.width, blockHeight, null);
            pdf.clip();
            pdf.discardPath();
            draw();
            pdf.restoreGraphicsState();
          };
          pdf.setTextColor(31, 41, 55);
          pdf.setFont("helvetica", "bold");
          pdf.setFontSize(8);
          drawInCell(recommendationColumns.recommendation, () => {
            pdf.text(titleLines, recommendationColumns.recommendation.x + 2, y + 5);
            pdf.setFont("helvetica", "normal");
            pdf.setFontSize(7);
            pdf.setTextColor(185, 28, 28);
            pdf.text(resourceLines, recommendationColumns.recommendation.x + 2, y + 7 + titleLines.length * 4);
          });
          drawInCell(recommendationColumns.benefit, () => {
            pdf.setFont("helvetica", "normal");
            pdf.setFontSize(7);
            pdf.setTextColor(37, 99, 235);
            pdf.text(benefitLines, recommendationColumns.benefit.x + 2, y + 5);
          });
          drawInCell(recommendationColumns.category, () => {
            pdf.setTextColor(75, 85, 99);
            pdf.text(categoryLines, recommendationColumns.category.x + 2, y + 5);
          });
          drawInCell(recommendationColumns.impact, () => {
            pdf.setTextColor(recommendation.impact === "High" ? 220 : 75, recommendation.impact === "High" ? 38 : 85, recommendation.impact === "High" ? 38 : 99);
            pdf.text(recommendation.impact, recommendationColumns.impact.x + 2, y + 5);
          });
          drawInCell(recommendationColumns.weight, () => {
            pdf.setTextColor(75, 85, 99);
            pdf.text(`${recommendation.contributionPercent.toFixed(1)}%`, recommendationColumns.weight.x + 2, y + 5);
          });
          y += blockHeight;
        });
      }

      const filenameDate = new Date().toISOString().slice(0, 10);
      pdf.save(`resiliency-evaluation-${filenameDate}.pdf`);
    } finally {
      setActiveTabIndex(previousTab);
    }
  }, [activeTabIndex]);

  const markSubscriptionDirty = useCallback((subscriptionId: string | null) => {
    if (!subscriptionId) return;
    setPendingRefreshSubscriptions(prev => {
      const next = new Set(prev);
      next.add(subscriptionId);
      return next;
    });
    setNeedsRefresh(true);
  }, []);

  const readStoredGraph = (): GraphSnapshot | null => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || !Array.isArray(parsed.nodes) || !Array.isArray(parsed.edges)) return null;
      return parsed as GraphSnapshot;
    } catch {
      return null;
    }
  };

  const persistGraph = (next: GraphSnapshot | null) => {
    if (!next) {
      localStorage.removeItem(storageKey);
      return;
    }
    try {
      localStorage.setItem(storageKey, JSON.stringify(next));
    } catch {
      /* ignore storage errors */
    }
  };

  const updateGraph = (updater: (prev: GraphSnapshot | null) => GraphSnapshot | null) => {
    setGraph(prev => {
      const base = prev ?? readStoredGraph();
      const next = updater(base);
      persistGraph(next);
      return next;
    });
  };

  const buildWorkloadViewState = useCallback((): WorkloadViewState => ({
    selected_subscriptions: selectedSubscriptionIds,
    view_level: viewLevel,
    ai_layer_enabled: aiLayerEnabled,
    user_layer_enabled: userLayerEnabled,
    resource_group_filter: Array.from(resourceGroupFilter),
    service_filter: Array.from(serviceFilter),
    expanded_categories: Array.from(expandedCategories),
    show_legend: showLegend,
    graph_view: graphCanvasRef.current?.getViewState() ?? undefined,
    service_group_filter: serviceGroupBinding ?? undefined,
    region_az_counts: regionAzCounts,
  }), [selectedSubscriptionIds, viewLevel, aiLayerEnabled, userLayerEnabled, resourceGroupFilter, serviceFilter, expandedCategories, showLegend, serviceGroupBinding, regionAzCounts]);

  const normalizeWorkloadViewState = useCallback((state: WorkloadViewState): WorkloadViewState => {
    const sort = (values: string[]) => [...values].map(String).sort();
    const normalizeRegionCounts = (counts?: Record<string, 1 | 2 | 3>) => {
      const entries = Object.entries(counts || {})
        .map(([region, azCount]) => [normalizeRegionKey(region), Number(azCount)] as const)
        .filter(([region, azCount]) => region.length > 0 && (azCount === 1 || azCount === 2 || azCount === 3))
        .sort(([a], [b]) => a.localeCompare(b));
      return Object.fromEntries(entries) as Record<string, 1 | 2 | 3>;
    };
    const normalizePositions = (positions?: Record<string, { x: number; y: number }>) => {
      if (!positions) return {} as Record<string, { x: number; y: number }>;
      return Object.fromEntries(
        Object.entries(positions)
          .map(([id, pos]) => [id, { x: pos.x, y: pos.y }])
          .sort(([a], [b]) => String(a).localeCompare(String(b)))
      );
    };
    return {
      selected_subscriptions: sort(state.selected_subscriptions || []),
      view_level: state.view_level || "overview",
      ai_layer_enabled: state.ai_layer_enabled ?? true,
      user_layer_enabled: state.user_layer_enabled ?? true,
      resource_group_filter: sort(state.resource_group_filter || []),
      service_filter: sort(state.service_filter || []),
      expanded_categories: sort(state.expanded_categories || []),
      show_legend: !!state.show_legend,
      graph_view: state.graph_view
        ? {
            viewport: state.graph_view.viewport
              ? { x: state.graph_view.viewport.x, y: state.graph_view.viewport.y, zoom: state.graph_view.viewport.zoom }
              : undefined,
            node_positions: normalizePositions(state.graph_view.node_positions),
          }
        : undefined,
      service_group_filter: state.service_group_filter
        ? {
            service_group_id: state.service_group_filter.service_group_id,
            service_group_name: state.service_group_filter.service_group_name,
            display_name: state.service_group_filter.display_name,
            parent_service_group_id: state.service_group_filter.parent_service_group_id ?? undefined,
            member_resource_ids: sort(state.service_group_filter.member_resource_ids || []),
          }
        : undefined,
      region_az_counts: normalizeRegionCounts(state.region_az_counts),
    };
  }, [normalizeRegionKey]);

  const isWorkloadDirty = useMemo(() => {
    if (!activeWorkloadId || !activeWorkloadState) return false;
    const current = normalizeWorkloadViewState(buildWorkloadViewState());
    const saved = normalizeWorkloadViewState(activeWorkloadState);
    return JSON.stringify(current) !== JSON.stringify(saved);
  }, [activeWorkloadId, activeWorkloadState, buildWorkloadViewState, normalizeWorkloadViewState, graphViewRevision]);

  const isNewWorkloadDirty = useMemo(() => {
    if (activeWorkloadId) return false;
    const current = normalizeWorkloadViewState(buildWorkloadViewState());
    const defaultState = normalizeWorkloadViewState({
      selected_subscriptions: [],
      view_level: "overview",
      ai_layer_enabled: true,
      user_layer_enabled: true,
      resource_group_filter: [],
      service_filter: [],
      expanded_categories: [],
      show_legend: false,
      region_az_counts: {},
    });
    return JSON.stringify(current) !== JSON.stringify(defaultState);
  }, [activeWorkloadId, buildWorkloadViewState, normalizeWorkloadViewState, graphViewRevision]);

  const applyWorkloadViewState = useCallback((state: WorkloadViewState) => {
    skipFilterResetRef.current = true;
    pendingWorkloadApplyRef.current = true;
    setPendingGraphView(state.graph_view ?? null);
    if (state.graph_view) {
      skipNextFitViewRef.current = true;
    }
    setSelectedSubscriptions(new Set(state.selected_subscriptions || []));
    setViewLevel((state.view_level as ViewLevel) || "overview");
    setAiLayerEnabled(state.ai_layer_enabled ?? true);
    setUserLayerEnabled(state.user_layer_enabled ?? true);
    setResourceGroupFilter(new Set(state.resource_group_filter || []));
    setServiceFilter(new Set(state.service_filter || []));
    setExpandedCategories(new Set(state.expanded_categories || []));
    setShowLegend(!!state.show_legend);
    setServiceGroupBinding(state.service_group_filter ?? null);
    setRegionAzCounts((state.region_az_counts || {}) as Record<string, 1 | 2 | 3>);
  }, []);

  const handleRegionAzCountChange = useCallback((region: string, azCount: 1 | 2 | 3) => {
    const key = normalizeRegionKey(region);
    if (!key) return;
    setRegionAzCounts(prev => ({ ...prev, [key]: azCount }));
  }, [normalizeRegionKey]);

  const workloadRegions = useMemo(() => {
    const regions = new Set<string>();
    for (const item of zonal_resilience_data?.resources || []) {
      const location = String(item.location || "").trim();
      if (location) regions.add(location);
    }
    return Array.from(regions).sort((a, b) => a.localeCompare(b));
  }, [zonal_resilience_data]);

  const hydrateServiceGroupBinding = useCallback(async (binding: ServiceGroupBinding | null): Promise<ServiceGroupBinding | null> => {
    if (!binding || binding.parent_service_group_id || !binding.service_group_id) return binding;
    try {
      const groups = await listAzureServiceGroups();
      const match = groups.find(group => group.id === binding.service_group_id || group.name === binding.service_group_name);
      if (!match?.parent_service_group_id) return binding;
      return { ...binding, parent_service_group_id: match.parent_service_group_id };
    } catch {
      return binding;
    }
  }, []);

  const loadWorkloads = useCallback(async () => {
    try {
      const list = await listWorkloads();
      setWorkloads(list);
    } catch (err: any) {
      setWorkloadError(err.message ?? "Failed to load workloads");
    }
  }, []);

  const applyResiliencyOverrides = useCallback((evaluations: Record<string, any>, overrides?: Record<string, any>) => {
    // Build lookup by resilience_check_id (deterministic UUIDv5 from resource_id + recommendation_id)
    // NOTE: We only override status; we intentionally keep the original validation_source so
    // PendingReview/User sources remain the same after an override.
    const overrideLookup = new Map<string, { status: "pass" | "fail" | "pending"; resilience_check_id?: string }>();

    Object.entries(overrides || {}).forEach(([resilienceCheckId, override]) => {
      if (!resilienceCheckId) return;

      overrideLookup.set(resilienceCheckId, {
        status: (override as any)?.status,
        resilience_check_id: resilienceCheckId,
      });
    });

    return Object.fromEntries(
      Object.entries(evaluations || {}).map(([resourceId, evaluation]) => {
        const checksOrFindings = (evaluation as any).findings || (evaluation as any).checks || [];

        const mergedChecks = checksOrFindings.map((check: any) => {
          const resilienceCheckId = check?.resilience_check_id as string | undefined;
          if (!resilienceCheckId) return check;

          const override = overrideLookup.get(resilienceCheckId);
          if (!override) return check;

          return {
            ...check,
            status: override.status,
            // Preserve the original validation_source to keep PendingReview/User intact
            validation_source: check.validation_source,
            resilience_check_id: resilienceCheckId,
          };
        });

        const passed = mergedChecks.filter((c: any) => c.status === "pass").length;
        const failed = mergedChecks.filter((c: any) => c.status === "fail").length;

        return [
          resourceId,
          {
            ...evaluation,
            findings: (evaluation as any).findings ? mergedChecks : undefined,
            checks: (evaluation as any).checks ? mergedChecks : undefined,
            passed_checks: passed,
            failed_checks: failed,
            total_checks: mergedChecks.length,
          },
        ];
      })
    );
  }, []);

  const applyAzConstraintNormalization = useCallback((evaluations: Record<string, any>) => {
    const normalizeRegionKey = (region: string): string => String(region || "").trim().toLowerCase().replace(/\s+/g, "");

    const getRequiredAzCountForRegion = (region: string): 1 | 2 | 3 => {
      const configured = regionAzCounts[normalizeRegionKey(region)];
      return configured ?? 3;
    };

    const getResourceLocation = (resourceId: string, evaluation: any): string => {
      const fromEvaluation = String(
        evaluation?.location
        || evaluation?.resource_location
        || evaluation?.resourceLocation
        || ""
      ).trim();
      if (fromEvaluation) return fromEvaluation;

      const node = graph?.nodes?.find(n => String(n.id).toLowerCase() === String(resourceId).toLowerCase());
      const metadata = (node?.metadata ?? {}) as Record<string, unknown>;
      const fromNode = String(metadata.location || metadata.region || "").trim();
      return fromNode;
    };

    const zoneKeywords = [
      "availability zone",
      "availability zones",
      "zone-redundant",
      "zone redundant",
      "zonal",
      "multi-zone",
      "cross-zone",
      "3-az",
      "third zone",
      "third availability zone",
    ];

    const isZoneRelatedText = (text?: string): boolean => {
      const source = String(text || "").toLowerCase();
      return zoneKeywords.some(keyword => source.includes(keyword));
    };

    const extractRequiredAzFromText = (text?: string): number | null => {
      const source = String(text || "").toLowerCase();
      if (!source) return null;

      if (source.includes("3-az") || source.includes("all 3 availability zones") || source.includes("third availability zone") || source.includes("third zone")) {
        return 3;
      }
      if (/need\s*3\+?/.test(source) || /require\w*\s*3\+?/.test(source)) {
        return 3;
      }
      if (/need\s*2\+?/.test(source) || /require\w*\s*2\+?/.test(source)) {
        return 2;
      }
      if (source.includes("across multiple availability zones") || source.includes("multi-zone") || source.includes("zone-redundant")) {
        return 2;
      }
      return null;
    };

    const hasNonZoneFailureSignal = (check: any): boolean => {
      const reasoningCandidates = [
        String(check?.heuristic_reasoning || ""),
        String(check?.llm_reasoning || ""),
      ].filter(Boolean);

      for (const candidate of reasoningCandidates) {
        const text = candidate.toLowerCase();
        if (!text.includes("failed:")) continue;
        const failedPart = text.split("failed:")[1] || "";
        const parts = failedPart.split("|").map(p => p.trim()).filter(Boolean);
        if (parts.length === 0) continue;

        const nonZoneParts = parts.filter(part => !isZoneRelatedText(part));
        if (nonZoneParts.length > 0) return true;
      }
      return false;
    };

    const splitFailedClauses = (text?: string): { prefix: string; clauses: string[] } => {
      const source = String(text || "");
      const lower = source.toLowerCase();
      const marker = "failed:";
      const idx = lower.indexOf(marker);
      if (idx < 0) {
        return { prefix: source.trim(), clauses: [] };
      }

      const prefix = source.slice(0, idx + marker.length).trim();
      const rest = source.slice(idx + marker.length).trim();
      const clauses = rest.length > 0
        ? rest.split("|").map(part => part.trim()).filter(Boolean)
        : [];
      return { prefix, clauses };
    };

    return Object.fromEntries(
      Object.entries(evaluations || {}).map(([resourceId, evaluation]) => {
        const location = getResourceLocation(resourceId, evaluation);
        const requiredAzCount = getRequiredAzCountForRegion(location);

        if (requiredAzCount >= 3) {
          return [resourceId, evaluation];
        }

        const adaptCheck = (check: any) => {
          const currentStatus = String(check?.status || "").toLowerCase();
          if (currentStatus !== "fail") return check;

          const texts = [
            check?.description,
            check?.long_description,
            check?.heuristic_reasoning,
            check?.llm_reasoning,
          ].map(v => String(v || ""));

          const isZoneRelated = texts.some(isZoneRelatedText)
            || String(check?.deployment_pattern || "").toLowerCase().includes("zone");
          if (!isZoneRelated) return check;

          const requiredByRule = texts
            .map(extractRequiredAzFromText)
            .filter((v): v is number => typeof v === "number")
            .reduce((max, v) => Math.max(max, v), 0);

          if (!requiredByRule || requiredByRule <= requiredAzCount) {
            if (!hasNonZoneFailureSignal(check)) {
              const adaptationNote = `Adapted due to regional AZ limit: rule expects ${requiredByRule} AZ(s), region configured for ${requiredAzCount} AZ(s).`;
              return {
                ...check,
                status: "pass",
                az_constraint_adapted: true,
                az_required_by_rule: requiredByRule,
                az_available_in_region: requiredAzCount,
                heuristic_reasoning: check?.heuristic_reasoning
                  ? `${check.heuristic_reasoning} | ${adaptationNote}`
                  : adaptationNote,
              };
            }

            // Mixed failure case: keep failed status, but remove AZ-only failed clauses
            // so users only see actionable non-AZ blockers.
            const originalReasoning = String(check?.heuristic_reasoning || "");
            if (!originalReasoning) return check;

            const { prefix, clauses } = splitFailedClauses(originalReasoning);
            if (clauses.length === 0) return check;

            const filteredClauses = clauses.filter(clause => !isZoneRelatedText(clause));
            const adaptationNote = `AZ-only failures were adapted for configured regional limit (${requiredAzCount} AZ).`;
            const rewrittenReasoning = filteredClauses.length > 0
              ? `${prefix} ${filteredClauses.join(" | ")} | ${adaptationNote}`
              : adaptationNote;

            return {
              ...check,
              az_constraint_adapted: true,
              az_required_by_rule: requiredByRule,
              az_available_in_region: requiredAzCount,
              heuristic_reasoning: rewrittenReasoning,
            };
          }

          return check;
        };

        const nextFindings = (evaluation?.findings || []).map(adaptCheck);
        const nextChecks = (evaluation?.checks || []).map(adaptCheck);
        const listForCounts = (nextChecks.length > 0 ? nextChecks : nextFindings) as any[];

        const passed = listForCounts.filter((c: any) => String(c?.status || "").toLowerCase() === "pass").length;
        const failed = listForCounts.filter((c: any) => String(c?.status || "").toLowerCase() === "fail").length;

        return [
          resourceId,
          {
            ...evaluation,
            findings: evaluation?.findings ? nextFindings : undefined,
            checks: evaluation?.checks ? nextChecks : undefined,
            passed_checks: passed,
            failed_checks: failed,
            total_checks: listForCounts.length,
          },
        ];
      })
    );
  }, [graph?.nodes, regionAzCounts]);

  const azNormalizedEvaluations = useMemo(() => {
    if (!resilience_evaluations) return null;
    return applyAzConstraintNormalization(resilience_evaluations);
  }, [resilience_evaluations, applyAzConstraintNormalization]);

  const upsertResiliencyOverride = useCallback((override: { resilience_check_id?: string; status: "pass" | "fail" | "pending"; overridden_by?: string; resource_id?: string; recommendation_id?: string }) => {
    const resilienceCheckId = override?.resilience_check_id;
    if (!resilienceCheckId) return;

    setResiliencyOverrides(prev => ({
      ...(prev || {}),
      [resilienceCheckId]: {
        ...(prev || {})[resilienceCheckId],
        ...override,
        overridden_by: override.overridden_by ?? "user",
      },
    }));
  }, []);

  const removeResiliencyOverride = useCallback((resilienceCheckId: string) => {
    if (!resilienceCheckId) return;

    setResiliencyOverrides(prev => {
      const next = { ...(prev || {}) };
      delete next[resilienceCheckId];
      return next;
    });
  }, []);

  const applyOptimisticOverrideRemoval = useCallback((resilienceCheckId: string, resourceId?: string) => {
    if (!resilienceCheckId || !resourceId) return;

    setResiliencyEvaluations(prev => {
      if (!prev || !prev[resourceId]) return prev;

      const entry = prev[resourceId];
      const updateList = (checks?: any[]) => {
        if (!checks) return checks;
        return checks.map(check => {
          if (check?.resilience_check_id !== resilienceCheckId) return check;

          const nextCheck = { ...check, status: "fail" as const };
          const validationSource = check?.validation_source;

          if (typeof validationSource === "string") {
            const lower = validationSource.toLowerCase();
            nextCheck.validation_source = lower === "user" || !validationSource ? "APRL" : validationSource;
          } else {
            nextCheck.validation_source = "APRL";
          }

          return nextCheck;
        });
      };

      const nextFindings = updateList((entry as any).findings);
      const nextChecks = updateList((entry as any).checks);
      const listForCounts = (nextChecks ?? nextFindings ?? []) as any[];

      return {
        ...prev,
        [resourceId]: {
          ...entry,
          findings: (entry as any).findings ? nextFindings : undefined,
          checks: (entry as any).checks ? nextChecks : undefined,
          passed_checks: listForCounts.filter((c: any) => c.status === "pass").length,
          failed_checks: listForCounts.filter((c: any) => c.status === "fail").length,
          total_checks: listForCounts.length,
        },
      };
    });
  }, []);

  const fetchGraph = useCallback(async () => {
    if (selectedSubscriptionIds.length === 0) return;
    try {
      setLoading(true);
      setError(null);

      const results = await Promise.all(
        selectedSubscriptionIds.map(async subscriptionId => {
          const raw = await fetchWorkloadGraph(subscriptionId);
          const normalized = normalizeGraph(raw);
          return { subscriptionId, raw, graph: normalized };
        })
      );

      const mergedGraph = mergeGraphSnapshots(
        results.map(item => ({ subscriptionId: item.subscriptionId, graph: item.graph }))
      );

      persistGraph(mergedGraph);
      setGraph(mergedGraph);

      const mergedEvaluations = mergeResiliencyEvaluations(
        results.map(item => ({
          subscriptionId: item.subscriptionId,
          evaluations: item.raw.resilience_evaluations?.evaluations || {},
        }))
      );

      const mergedOverrides = results.reduce<Record<string, any>>((acc, item) => {
        return { ...acc, ...(item.raw.resilience_overrides || {}) };
      }, {});

      if (Object.keys(mergedEvaluations).length > 0) {
        setResiliencyEvaluations(mergedEvaluations);
        setResiliencyData({ evaluations: mergedEvaluations });
      } else {
        setResiliencyEvaluations(null);
        setResiliencyData(null);
      }

      setResiliencyOverrides(mergedOverrides);

      if (skipFilterResetRef.current) {
        skipFilterResetRef.current = false;
      } else {
        // Reset filters after loading new graph data so all options are checked
        serviceFilterUserTouchedRef.current = false;
        resourceGroupFilterUserTouchedRef.current = false;
        validationSourceFilterUserTouchedRef.current = false;
        setServiceFilter(new Set());
        setResourceGroupFilter(new Set());
        setValidationSourceFilter(new Set());
        setExpandedCategories(new Set());
      }
    } catch (err: any) {
      setError(err.message ?? "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [selectedSubscriptionIds, storageKey]);

  const handleOverrideSaved = useCallback((override: { resilience_check_id?: string; status: "pass" | "fail" | "pending"; overridden_by?: string; resource_id?: string; recommendation_id?: string }) => {
    upsertResiliencyOverride(override);
    // Validation overrides should not trigger Refresh Annotations & Scores
  }, [upsertResiliencyOverride]);

  const handleOverrideDeleted = useCallback((resilienceCheckId: string, resourceId?: string) => {
    removeResiliencyOverride(resilienceCheckId);
    applyOptimisticOverrideRemoval(resilienceCheckId, resourceId);
    // Validation overrides should not trigger Refresh Annotations & Scores
  }, [removeResiliencyOverride, applyOptimisticOverrideRemoval]);

  const handleOpenResourceDetails = (resourceId: string) => {
    const targetResourceId = String(resourceId || "").trim();
    if (!targetResourceId) return;

    const allNodes = [...(viewGraph?.nodes ?? []), ...(graph?.nodes ?? [])];
    const matchedNode = allNodes.find((node) =>
      String(node?.id ?? "").toLowerCase() === targetResourceId.toLowerCase()
    );
    if (!matchedNode) return;

    setSelectedNode(buildSelectedNodeData(matchedNode));
    setSelectedEdge(null);
    setActiveSubscriptionId(resolveSubscriptionIdForNode(matchedNode.id));
  };

  const handleShowInGraph = (resourceId: string) => {
    const targetResourceId = String(resourceId || "").trim();
    if (!targetResourceId) return;

    const matchedNode = (graph?.nodes ?? []).find((node) =>
      String(node?.id ?? "").toLowerCase() === targetResourceId.toLowerCase()
    );
    const resolvedNodeId = matchedNode?.id ?? targetResourceId;

    suppressNextNodeDrawerOpenRef.current = true;

    setActiveTabIndex(0);

    let attempts = 0;
    const maxAttempts = 20;

    const trySelectNode = () => {
      const canvas = graphCanvasRef.current;
      if (canvas) {
        canvas.selectNode(resolvedNodeId);
        return;
      }

      attempts += 1;
      if (attempts < maxAttempts) {
        window.setTimeout(trySelectNode, 50);
      }
    };

    window.setTimeout(trySelectNode, 0);
  };

  const handleChatResourceHighlight = (resourceIds: string[]) => {
    if (!resourceIds.length) return;
    handleOpenResourceDetails(resourceIds[0]);
  };

  const fetchZonalResiliency = useCallback(async () => {
    if (selectedSubscriptionIds.length === 0) return;
    try {
      setZonalResiliencyLoading(true);
      setZonalResiliencyError(null);

      const dataList = await Promise.all(
        selectedSubscriptionIds.map(async subscriptionId => ({
          subscriptionId,
          data: await getZonalResiliency(subscriptionId),
        }))
      );

      setZonalResiliencyData(mergeZonalResiliencyData(dataList));
    } catch (err: any) {
      setZonalResiliencyError(err.message ?? "Failed to load zonal resilience data");
    } finally {
      setZonalResiliencyLoading(false);
    }
  }, [selectedSubscriptionIds]);

  const loadMappedSubscriptions = useCallback(async (restoreSelection: boolean = false) => {
    const subs = await fetchSubscriptions();
    setSubscriptions(subs);

    if (!restoreSelection) {
      return;
    }

    const storedMulti = localStorage.getItem("awg_subscription_ids");
    let restored: string[] = [];

    if (storedMulti) {
      try {
        const parsed = JSON.parse(storedMulti);
        if (Array.isArray(parsed)) restored = parsed.map(String);
      } catch {
        restored = [];
      }
    }

    if (restored.length === 0) {
      const storedSingle = localStorage.getItem("awg_subscription_id");
      if (storedSingle) restored = [storedSingle];
    }

    const valid = restored.filter(id => subs.some(s => s.id === id));
    setSelectedSubscriptions(new Set(valid));

    if (valid.length === 0) {
      localStorage.removeItem("awg_subscription_id");
      localStorage.removeItem("awg_subscription_ids");
    }
  }, []);

  const loadAvailableSubscriptionsForMapping = useCallback(async () => {
    const discovered = await discoverSubscriptions();
    setAvailableSubscriptionsForMapping(discovered);
    setMappingError(null);
    setMappingAuthRequired(false);
  }, []);

  // Fetch subscriptions on mount
  useEffect(() => {
    loadMappedSubscriptions(true).catch(err => {
      console.error("Failed to fetch subscriptions:", err);
    });
    loadAvailableSubscriptionsForMapping().catch(err => {
      console.error("Failed to discover subscriptions:", err);
      setAvailableSubscriptionsForMapping([]);
      setMappingError(err?.message ?? "Unable to discover Azure subscriptions. Authenticate first and try again.");
      setMappingAuthRequired(err?.code === "AZURE_AUTH_REQUIRED");
    });
  }, [loadMappedSubscriptions, loadAvailableSubscriptionsForMapping]);

  // Check chat availability on mount
  useEffect(() => {
    LLMChatService.isChatAvailable()
      .then(result => {
        setIsChatAvailable(result.available);
        setChatAvailabilityChecked(true);
        if (!result.available) {
          console.warn('Chat feature is disabled:', result.reason);
        }
      })
      .catch(err => {
        console.error('Error checking chat availability:', err);
        setIsChatAvailable(false);
        setChatAvailabilityChecked(true);
      });
  }, []);

  useEffect(() => {
    loadWorkloads();
  }, [loadWorkloads]);

  // Fetch weights from backend
  useEffect(() => {
    const loadWeights = async () => {
      try {
        const response = await fetch('/api/resilience/weights');
        if (response.ok) {
          const data = await response.json();
          setResiliencyWeights({
            categoryWeights: data.category_weights || DEFAULT_WEIGHTS.categoryWeights,
            impactWeights: data.impact_weights || DEFAULT_WEIGHTS.impactWeights,
          });
        }
      } catch (error) {
        console.error('Failed to load weights from backend, using defaults:', error);
      }
    };
    loadWeights();
  }, []);

  const prevViewLevelRef = useRef<ViewLevel>("overview");

  // Fit view when filters change (but NOT when view level changes - resetLayout handles that)
  useEffect(() => {
    const viewLevelChanged = prevViewLevelRef.current !== viewLevel;
    prevViewLevelRef.current = viewLevel;

    if (skipNextFitViewRef.current) {
      skipNextFitViewRef.current = false;
      return;
    }
    
    // Skip if view level just changed - resetLayout will handle viewport
    if (viewLevelChanged) {
      return;
    }
    
    // Only fitView when filters change, not when view level changes
    graphCanvasRef.current?.fitView();
  }, [viewLevel, serviceFilter, resourceGroupFilter]);

  useEffect(() => {
    if (!graph) return;
  }, [graph]);

  // Clear selections when view level changes
  useEffect(() => {
    setSelectedNode(null);
    setSelectedEdge(null);
  }, [viewLevel]);

  const resourceGroupOptions = useMemo(() => {
    if (!graph) return [] as { key: string; label: string }[];
    return computeResourceGroupOptions(graph, viewLevel, aiLayerEnabled, userLayerEnabled);
  }, [graph, viewLevel, aiLayerEnabled, userLayerEnabled]);

  const handleResourceGroupFilterChange = useCallback((next: Set<string>) => {
    resourceGroupFilterUserTouchedRef.current = true;
    setResourceGroupFilter(next);
  }, []);

  const serviceOptions = useMemo(() => {
    if (!graph) return [];
    return computeServiceOptions(graph, viewLevel, aiLayerEnabled, userLayerEnabled);
  }, [graph, viewLevel, aiLayerEnabled, userLayerEnabled]);

  const handleServiceFilterChange = useCallback((next: Set<string>) => {
    serviceFilterUserTouchedRef.current = true;
    setServiceFilter(next);
  }, []);

  const validationSourceOptions = useMemo(() => {
    return computeValidationSourceOptions(resilience_evaluations);
  }, [resilience_evaluations]);

  const handleValidationSourceFilterChange = useCallback((next: Set<string>) => {
    validationSourceFilterUserTouchedRef.current = true;
    setValidationSourceFilter(next);
  }, []);

  useEffect(() => {
    if (!serviceOptions.length) {
      if (expandedCategories.size) setExpandedCategories(new Set());
      return;
    }

    // Auto-populate serviceFilter if it's empty (after subscription change or reset)
    if (!serviceFilterUserTouchedRef.current && serviceFilter.size === 0) {
      const allServices = serviceOptions.flatMap(cat => cat.services.map(s => s.key));
      setServiceFilter(new Set(allServices));
      return;
    }

    const mixedCategories = new Set<string>();
    serviceOptions.forEach(category => {
      const allServicesInCategory = category.services.map(s => s.key);
      const selectedCount = allServicesInCategory.filter(key => serviceFilter.has(key)).length;
      if (selectedCount > 0 && selectedCount < allServicesInCategory.length) {
        mixedCategories.add(category.category);
      }
    });

    setExpandedCategories(mixedCategories);
  }, [serviceOptions, serviceFilter]);
  useEffect(() => {
    if (!resourceGroupOptions.length) {
      if (pendingWorkloadApplyRef.current) return;
      if (resourceGroupFilter.size) setResourceGroupFilter(new Set());
      return;
    }

    if (pendingWorkloadApplyRef.current) {
      pendingWorkloadApplyRef.current = false;
      return;
    }

    // Auto-populate resourceGroupFilter if it's empty (after subscription change or reset)
    if (!resourceGroupFilterUserTouchedRef.current && resourceGroupFilter.size === 0) {
      const allGroups = resourceGroupOptions.map(rg => rg.key);
      setResourceGroupFilter(new Set(allGroups));
      return;
    }

    // Keep the user's current selection; avoid shrinking it when the option list changes.
    // This prevents transient option recalculation from hiding nodes unexpectedly.
  }, [resourceGroupOptions, resourceGroupFilter.size]);
  useEffect(() => {
    if (!validationSourceOptions.length) {
      if (validationSourceFilter.size) setValidationSourceFilter(new Set());
      return;
    }

    // Auto-populate only if user hasn't intentionally changed it (e.g., uncheck all)
    if (!validationSourceFilterUserTouchedRef.current && validationSourceFilter.size === 0) {
      const allSources = validationSourceOptions.map(s => s.key);
      setValidationSourceFilter(new Set(allSources));
      return;
    }
  }, [validationSourceOptions, validationSourceFilter.size]);

  // Build annotation map for element weight lookup
  const annotationMap = useMemo(() => {
    if (!graph?.llm_annotations?.nodes) return new Map();
    const map = new Map<string, any>();
    for (const node of graph.llm_annotations.nodes) {
      map.set(node.node_id.toLowerCase(), node.annotations);
    }
    return map;
  }, [graph]);

  // Merge overrides into evaluations for scoring without mutating cached graph
  const mergedEvaluations = useMemo(() => {
    if (!azNormalizedEvaluations) return null;
    return applyResiliencyOverrides(azNormalizedEvaluations, resilience_overrides);
  }, [azNormalizedEvaluations, resilience_overrides, applyResiliencyOverrides]);

  // Enrich graph with calculated resilience scores (SINGLE CALCULATION POINT)
  const graphWithScores = useMemo(() => {
    if (!graph || !mergedEvaluations) return graph;

    // Create a case-insensitive lookup map
    const evalLookup = new Map<string, any>();
    Object.entries(mergedEvaluations).forEach(([key, value]) => {
      evalLookup.set(key.toLowerCase(), value);
    });

    const updatedNodes = graph.nodes.map(node => {
      const nodeId = node.id.toLowerCase();
      const evaluation = evalLookup.get(nodeId);
      
      if (!evaluation) {
        // No evaluation available - don't add resilience data
        return node;
      }

      const metadata = (node.metadata as any) || {};
      const resilience = metadata.resilience || {};

      const checks = evaluation.checks || evaluation.findings || [];
      if (checks.length === 0) {
        // No checks - don't add resilience data
        return node;
      }

      const passedChecks = typeof evaluation.passed_checks === "number"
        ? evaluation.passed_checks
        : checks.filter((check: any) => String(check?.status || "").toLowerCase() === "pass").length;
      const failedChecks = typeof evaluation.failed_checks === "number"
        ? evaluation.failed_checks
        : checks.filter((check: any) => String(check?.status || "").toLowerCase() === "fail").length;
      const totalChecks = typeof evaluation.total_checks === "number"
        ? evaluation.total_checks
        : checks.length;

      const elementWeight = getElementWeight(nodeId, annotationMap);
      const score = calculateResiliencyScore(checks, elementWeight, resilienceWeights);

      return {
        ...node,
        element_weight: elementWeight,
        metadata: {
          ...metadata,
          resilience: {
            ...resilience,
            resilience_score: score,
            checks,
            passed_checks: passedChecks,
            failed_checks: failedChecks,
            total_checks: totalChecks,
          },
        },
      };
    });

    return {
      ...graph,
      nodes: updatedNodes,
    };
  }, [graph, mergedEvaluations, annotationMap, resilienceWeights]);

  const viewGraph = useMemo(() => {
    if (!graphWithScores) return null;
    return buildViewGraph({
      snapshot: graphWithScores,
      aiLayerEnabled,
      userLayerEnabled,
      serviceFilter,
      resourceGroupFilter,
    });
  }, [graphWithScores, aiLayerEnabled, userLayerEnabled, serviceFilter, resourceGroupFilter]);

  const nodesForView = viewGraph?.nodes ?? graph?.nodes ?? [];
  const edgesForView = viewGraph?.edges ?? graph?.edges ?? [];
  const hasSelection = selectedSubscriptionIds.length > 0;

  const pdfReport = useMemo<ResiliencyPdfReport | null>(() => {
    if (!graph || !mergedEvaluations || nodesForView.length === 0) return null;

    const graphNodeMap = new Map(graph.nodes.map(node => [node.id.toLowerCase(), node]));
    const categories = new Map<string, { totalWeight: number; passedWeight: number; passed: number; failed: number }>();
    const impacts = new Map<string, { totalWeight: number; passedWeight: number; passed: number; failed: number }>();
    const services = new Map<string, { totalWeight: number; passedWeight: number; passed: number; failed: number }>();
    const recommendations = new Map<string, {
      title: string;
      benefit: string;
      category: string;
      impact: string;
      resources: Set<string>;
      failedWeight: number;
    }>();
    let totalWeight = 0;
    let passedWeight = 0;
    let totalChecks = 0;
    let passedChecks = 0;
    let failedChecks = 0;

    const resourceMatchesReportFilters = (resourceId: string, evaluation: any): boolean => {
      const graphNode = graphNodeMap.get(resourceId.toLowerCase());
      if (graphNode) {
        const annotation = annotationMap.get(resourceId.toLowerCase());
        const maxImportance = LEVEL_TO_MAX_IMPORTANCE[viewLevel];
        const importance = annotation?.layer ?? (graphNode.metadata as any)?.importance ?? 3;
        if (importance > maxImportance) return false;
      }

      if (resourceGroupFilter.size === 0 || serviceFilter.size === 0) return false;

      const evaluationResourceGroup = (evaluation?.resource_group || evaluation?.resource_group_name || evaluation?.resourceGroup || "")
        .toString()
        .toLowerCase();
      const resourceIdParts = resourceId.split("/").filter(Boolean);
      const resourceIdPartsLower = resourceIdParts.map(part => part.toLowerCase());
      const resourceGroupIndex = resourceIdPartsLower.indexOf("resourcegroups");
      const resourceGroup = evaluationResourceGroup || (
        resourceGroupIndex >= 0 && resourceGroupIndex + 1 < resourceIdParts.length
          ? resourceIdParts[resourceGroupIndex + 1].toLowerCase()
          : ""
      );
      if (resourceGroup && !resourceGroupFilter.has(resourceGroup)) return false;

      const serviceKey = graphNode
        ? canonicalTypeForNode(graphNode as any)
        : normalizeTypeString(evaluation?.resource_type);
      const normalizedServiceKey = String(serviceKey || "").toLowerCase();
      if (normalizedServiceKey && !serviceFilter.has(normalizedServiceKey)) return false;

      return true;
    };

    Object.entries(mergedEvaluations).forEach(([resourceId, evaluation]: [string, any]) => {
      if (!resourceMatchesReportFilters(resourceId, evaluation)) return;
      const checks = (evaluation.findings || evaluation.checks || []).filter((check: any) => {
        if (validationSourceFilter.size === 0) return true;
        return validationSourceFilter.has(check.validation_source || "");
      });
      const elementWeight = getElementWeight(resourceId, annotationMap);
      const annotation = annotationMap.get(resourceId.toLowerCase());
      const graphNode = graphNodeMap.get(resourceId.toLowerCase());
      const resourceName = annotation?.display_name || evaluation.resource_name || graphNode?.name || resourceId.split("/").filter(Boolean).pop() || resourceId;
      const serviceName = annotation?.azure_service_category || "Other";

      checks.forEach((check: any) => {
        const category = check.category || "Other";
        const rawWeight = elementWeight
          * (resilienceWeights.categoryWeights[category] ?? 0.05)
          * (resilienceWeights.impactWeights[check.impact] ?? 0.1);
        totalChecks += 1;
        totalWeight += rawWeight;

        const categoryStats = categories.get(category) || { totalWeight: 0, passedWeight: 0, passed: 0, failed: 0 };
        const impactName = check.impact || "Unknown";
        const impactStats = impacts.get(impactName) || { totalWeight: 0, passedWeight: 0, passed: 0, failed: 0 };
        const serviceStats = services.get(serviceName) || { totalWeight: 0, passedWeight: 0, passed: 0, failed: 0 };
        categoryStats.totalWeight += rawWeight;
        impactStats.totalWeight += rawWeight;
        serviceStats.totalWeight += rawWeight;
        if (check.status === "pass") {
          passedChecks += 1;
          passedWeight += rawWeight;
          categoryStats.passed += 1;
          categoryStats.passedWeight += rawWeight;
          impactStats.passed += 1;
          impactStats.passedWeight += rawWeight;
          serviceStats.passed += 1;
          serviceStats.passedWeight += rawWeight;
        } else {
          categoryStats.failed += 1;
          impactStats.failed += 1;
          serviceStats.failed += 1;
          if (check.status !== "fail") {
            categories.set(category, categoryStats);
            impacts.set(impactName, impactStats);
            services.set(serviceName, serviceStats);
            return;
          }
          failedChecks += 1;
          const recommendationId = check.recommendation_id || check.description;
          const recommendation = recommendations.get(recommendationId) || {
            title: check.description || recommendationId,
            benefit: check.potential_benefits || "Not specified",
            category,
            impact: check.impact || "Unknown",
            resources: new Set<string>(),
            failedWeight: 0,
          };
          recommendation.resources.add(resourceName);
          recommendation.failedWeight += rawWeight;
          recommendations.set(recommendationId, recommendation);
        }
        categories.set(category, categoryStats);
        impacts.set(impactName, impactStats);
        services.set(serviceName, serviceStats);
      });
    });

    const toBreakdown = (entries: typeof categories) => Array.from(entries.entries())
      .map(([name, item]) => ({
        name,
        score: item.totalWeight > 0 ? item.passedWeight / item.totalWeight : 0,
        passed: item.passed,
        failed: item.failed,
        totalWeight: item.totalWeight,
      }))
      .sort((a, b) => b.totalWeight - a.totalWeight)
      .map(({ totalWeight: _totalWeight, ...item }) => item);

    return {
      score: totalWeight > 0 ? passedWeight / totalWeight : 0,
      totalChecks,
      passedChecks,
      failedChecks,
      categories: toBreakdown(categories),
      impacts: toBreakdown(impacts),
      services: toBreakdown(services),
      recommendations: Array.from(recommendations.values())
        .sort((a, b) => b.failedWeight - a.failedWeight)
        .slice(0, 10)
        .map(recommendation => ({
          title: recommendation.title,
          benefit: recommendation.benefit,
          category: recommendation.category,
          impact: recommendation.impact,
          resources: Array.from(recommendation.resources),
          contributionPercent: totalWeight > 0 ? (recommendation.failedWeight / totalWeight) * 100 : 0,
        })),
    };
  }, [graph, mergedEvaluations, nodesForView, validationSourceFilter, annotationMap, resilienceWeights, viewLevel, resourceGroupFilter, serviceFilter]);

  // Auto layout once when the view graph is recomputed
  const layoutResetKeyRef = useRef<string>("");
  useEffect(() => {
    if (!viewGraph || nodesForView.length === 0) return;

    const key = `${viewLevel}:${viewGraph.nodes.length}:${viewGraph.edges.length}`;
    if (layoutResetKeyRef.current === key) return;
    layoutResetKeyRef.current = key;

    // Run after the current render cycle to ensure nodes/edges/groups are mounted
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        graphCanvasRef.current?.resetLayout();
      });
    });
  }, [viewGraph, viewLevel, nodesForView.length]);

  const resolveSubscriptionIdForEdge = useCallback((edgeId: string): string | null => {
    // Edges are always stored in the source node's subscription
    const edge = graph?.edges.find(e => e.id === edgeId) ?? viewGraph?.edges.find(e => e.id === edgeId);
    if (!edge) {
      setError("Edge not found.");
      return null;
    }
    
    // Use the source node's subscription (where the edge is stored)
    const sourceNode = graph?.nodes.find(n => n.id === edge.source) ?? viewGraph?.nodes.find(n => n.id === edge.source);
    const sourceSub = (sourceNode as any)?.subscription_id ?? (sourceNode?.metadata as any)?.subscription_id;
    if (sourceSub) return String(sourceSub);
    
    // Fallback to single subscription only if source node lookup fails
    if (singleSubscriptionId) return singleSubscriptionId;
    
    setError("Unable to determine subscription for edge source node. Please select a single subscription.");
    return null;
  }, [graph, viewGraph, singleSubscriptionId]);

  const resolveSubscriptionIdForNode = useCallback((nodeId: string): string | null => {
    const node = graph?.nodes.find(n => n.id === nodeId) ?? viewGraph?.nodes.find(n => n.id === nodeId);
    const nodeSub = (node as any)?.subscription_id ?? (node?.metadata as any)?.subscription_id;
    if (nodeSub) return String(nodeSub);
    if (singleSubscriptionId) return singleSubscriptionId;
    setError("Select a single subscription to modify resources.");
    return null;
  }, [graph, viewGraph, singleSubscriptionId]);

  const resolveSubscriptionIdForNodeIds = useCallback((nodeIds: string[]): string | null => {
    const subs = new Set<string>();
    nodeIds.forEach(nodeId => {
      const node = graph?.nodes.find(n => n.id === nodeId) ?? viewGraph?.nodes.find(n => n.id === nodeId);
      const nodeSub = (node as any)?.subscription_id ?? (node?.metadata as any)?.subscription_id;
      if (nodeSub) subs.add(String(nodeSub));
    });

    if (subs.size === 1) return Array.from(subs)[0];
    if (subs.size === 0 && singleSubscriptionId) return singleSubscriptionId;

    setError("Cross-subscription edits are not supported.");
    return null;
  }, [graph, viewGraph, singleSubscriptionId]);

  const getResourceLabel = useCallback((nodeId: string): string | undefined => {
    const node = graph?.nodes.find(n => n.id === nodeId) ?? viewGraph?.nodes.find(n => n.id === nodeId);
    if (!node) return undefined;
    const meta = (node as any)?.metadata ?? (node as any)?.data ?? {};
    return meta.display_name || meta.label || (node as any)?.name || nodeId;
  }, [graph, viewGraph]);

  const mentionableResources = useMemo(() => {
    const sourceNodes = (graph?.nodes && graph.nodes.length > 0)
      ? graph.nodes
      : (viewGraph?.nodes || []);

    const seen = new Set<string>();
    const items: Array<{ id: string; label?: string }> = [];

    sourceNodes.forEach((node: any) => {
      const nodeId = String(node?.id || '').trim();
      if (!nodeId) return;
      const key = nodeId.toLowerCase();
      if (seen.has(key)) return;
      seen.add(key);

      const meta = (node?.metadata ?? node?.data ?? {}) as any;
      const label = meta.display_name || meta.label || node?.name || undefined;
      items.push({ id: nodeId, label });
    });

    return items;
  }, [graph, viewGraph]);

  const chatTabContext = useMemo<"graph" | "overview" | "workloads">(() => {
    if (activeTabIndex === 1) return "overview";
    if (activeTabIndex === 0) return "graph";
    return "workloads";
  }, [activeTabIndex]);

  const chatContext = useMemo(() => ({
    tab: chatTabContext,
    selected_resource_id: selectedNode?.id,
    selected_edge_id: chatTabContext === "graph" ? selectedEdge?.id : undefined,
    view_level: chatTabContext === "overview" ? viewLevel : undefined,
    selected_subscriptions: selectedSubscriptionIds,
    active_workload_id: activeWorkloadId,
  }), [chatTabContext, selectedNode?.id, selectedEdge?.id, viewLevel, selectedSubscriptionIds, activeWorkloadId]);

  // Persist subscription selection
  useEffect(() => {
    if (selectedSubscriptionIds.length > 0) {
      localStorage.setItem("awg_subscription_ids", JSON.stringify(selectedSubscriptionIds));
      if (selectedSubscriptionIds.length === 1) {
        localStorage.setItem("awg_subscription_id", selectedSubscriptionIds[0]);
      } else {
        localStorage.removeItem("awg_subscription_id");
      }
    } else {
      localStorage.removeItem("awg_subscription_ids");
      localStorage.removeItem("awg_subscription_id");
    }
  }, [selectedSubscriptionIds]);

  // Hydrate from local storage for this subscription, then fetch fresh graph
  useEffect(() => {
    if (selectedSubscriptionIds.length === 0) {
      setGraph(null);
      setResiliencyEvaluations(null);
      setResiliencyOverrides({});
      setResiliencyData(null);
      setZonalResiliencyData(null);
      setPendingRefreshSubscriptions(new Set());
      setNeedsRefresh(false);
      return;
    }

    const stored = readStoredGraph();
    setGraph(stored);
    fetchGraph();
    fetchZonalResiliency();
  }, [selectionKey, selectedSubscriptionIds.length, fetchGraph, fetchZonalResiliency]);

  // Accept edge
  const handleAcceptEdge = async (edgeId: string) => {
    try {
      const edgeSubscriptionId = resolveSubscriptionIdForEdge(edgeId);
      if (!edgeSubscriptionId) return;

      await acceptEdge(edgeSubscriptionId, edgeId);

      // Mark as needing refresh
      markSubscriptionDirty(edgeSubscriptionId);

      // Optimistic UI update
      updateGraph(prev =>
        prev
          ? {
              ...prev,
              edges: prev.edges.map(e =>
                e.id === edgeId
                  ? { ...e, status: "accepted" }
                  : e
              )
            }
          : prev
      );

      setSelectedEdge(prev =>
        prev && prev.id === edgeId
          ? { ...prev, status: "accepted" }
          : prev
      );
    } catch (err) {
      console.error("Failed to accept edge", err);
    }
  };

  // Reject edge
  const handleRejectEdge = async (edgeId: string) => {
    try {
      const edgeSubscriptionId = resolveSubscriptionIdForEdge(edgeId);
      if (!edgeSubscriptionId) return;

      await rejectEdge(edgeSubscriptionId, edgeId);

      markSubscriptionDirty(edgeSubscriptionId);

      updateGraph(prev =>
        prev
          ? {
              ...prev,
              edges: prev.edges.map(e =>
                e.id === edgeId
                  ? { ...e, status: "rejected" }
                  : e
              )
            }
          : prev
      );

      setSelectedEdge(prev =>
        prev && prev.id === edgeId
          ? { ...prev, status: "rejected" }
          : prev
      );
    } catch (err) {
      console.error("Failed to reject edge", err);
    }
  };

  const handleDeleteEdge = async (edgeId: string) => {
    try {
      const edgeSubscriptionId = resolveSubscriptionIdForEdge(edgeId);
      if (!edgeSubscriptionId) return;

      await deleteEdge(edgeSubscriptionId, edgeId);

      markSubscriptionDirty(edgeSubscriptionId);

      updateGraph(prev =>
        prev
          ? {
              ...prev,
              edges: prev.edges.filter(e => e.id !== edgeId)
            }
          : prev
      );

      setSelectedEdge(prev =>
        prev && prev.id === edgeId ? null : prev
      );
    } catch (err) {
      console.error("Failed to delete edge", err);
    }
  };

  const handleReverseEdgeDirection = async (edgeId: string) => {
    try {
      const edge = graph?.edges.find(e => e.id === edgeId) ?? viewGraph?.edges.find(e => e.id === edgeId);
      if (!edge) return;

      const oldSourceSub = resolveSubscriptionIdForNode(edge.source);
      const newSourceSub = resolveSubscriptionIdForNode(edge.target);

      if (!oldSourceSub || !newSourceSub) return;

      if (oldSourceSub === newSourceSub) {
        const result = await reverseEdgeDirection(oldSourceSub, edgeId);
        const reversedEdge = result.edge;

        markSubscriptionDirty(oldSourceSub);

        if (reversedEdge) {
          const updated: GraphEdge = {
            id: reversedEdge.id,
            source: reversedEdge.source,
            target: reversedEdge.target,
            relationship: reversedEdge.relationship,
            confidence: reversedEdge.confidence,
            status: reversedEdge.status as any,
            origin: reversedEdge.origin,
            evidence: reversedEdge.evidence,
          };

          setSelectedEdge(updated);

          updateGraph(prev =>
            prev
              ? {
                  ...prev,
                  edges: prev.edges.map(e =>
                    e.id === edgeId ? updated : e
                  )
                }
              : prev
          );
        }

        return;
      }

      await deleteEdge(oldSourceSub, edgeId);
      markSubscriptionDirty(oldSourceSub);

      const created = await createManualEdge(newSourceSub, {
        source: edge.target,
        target: edge.source,
        relationship: edge.relationship,
      });

      const newEdge = created.edge;
      if (!newEdge) return;

      markSubscriptionDirty(newSourceSub);

      const updated: GraphEdge = {
        id: newEdge.id,
        source: newEdge.source,
        target: newEdge.target,
        relationship: newEdge.relationship,
        confidence: newEdge.confidence,
        status: newEdge.status as any,
        origin: newEdge.origin,
        evidence: newEdge.evidence,
      };

      setSelectedEdge(updated);

      updateGraph(prev =>
        prev
          ? {
              ...prev,
              edges: [...prev.edges.filter(e => e.id !== edgeId), updated],
            }
          : prev
      );
    } catch (err) {
      console.error("Failed to reverse edge direction", err);
    }
  };

  const buildSelectedNodeData = (node: GraphNode) => {
    const meta = (node.metadata as any) ?? {};
    const userOverride = (meta.user_override as Record<string, unknown> | undefined) ?? {};
    return {
      id: node.id,
      name: node.name,
      type: node.type,
      layer: meta.importance as number | undefined,
      color: meta.color as string | undefined,
      icon: meta.icon as string | undefined,
      override: Object.keys(userOverride).length > 0,
      criticalityScore: meta.criticality_score as number | undefined,
      aiAnnotation: node.metadata?.ai_annotation as any,
      originalName: node.name,
      raw: node,
    };
  };

  const handleNodeSelected = (nodeId: string | null) => {
    if (!nodeId) {
      setSelectedNode(null);
      setSelectedEdge(null);
      setActiveSubscriptionId(null);
      return;
    }
    if (suppressNextNodeDrawerOpenRef.current) {
      suppressNextNodeDrawerOpenRef.current = false;
      setSelectedNode(null);
      setSelectedEdge(null);
      setActiveSubscriptionId(resolveSubscriptionIdForNode(nodeId));
      return;
    }
    if (!viewGraph) return;

    const node = viewGraph.nodes.find(n => n.id === nodeId);
    if (!node) return;
    setSelectedNode(buildSelectedNodeData(node));
    setSelectedEdge(null);
    setActiveSubscriptionId(resolveSubscriptionIdForNode(nodeId));
  };

  const handleCreateManualLink = async (sourceId: string, targetId: string) => {
    if (!sourceId || !targetId || sourceId === targetId) return;

    try {
      const edgeSubscriptionId = resolveSubscriptionIdForNode(sourceId);
      if (!edgeSubscriptionId) return;

      const body = await createManualEdge(edgeSubscriptionId, {
        source: sourceId,
        target: targetId,
        relationship: "depends_on",
      });
      const created = body.edge;

      if (created) {
        // Mark as needing refresh
        markSubscriptionDirty(edgeSubscriptionId);

        const newEdge: GraphEdge = {
          id: created.id,
           source: created.source,
           target: created.target,
          relationship: created.relationship,
          confidence: created.confidence,
          status: created.status as any,
           origin: created.origin,
          evidence: created.evidence,
        };

        updateGraph(prev =>
          prev
            ? {
                ...prev,
                edges: [...prev.edges, newEdge],
              }
            : prev
        );

        // Open the drawer for the newly created edge
        setSelectedEdge(newEdge);
      } else {
        console.error("Failed to create link: no edge returned");
      }
    } catch (err: any) {
      console.error("Failed to create link:", err.message);
    }
  };

  const handleHideNode = async (nodeId: string) => {
    const nodeSubscriptionId = resolveSubscriptionIdForNode(nodeId);
    if (!nodeSubscriptionId) return;

    try {
      const response = await patchNode(nodeSubscriptionId, nodeId, { hidden: true });

      // Optimistic local updates: drop node, associated edges, group membership, and resilience data
      updateGraph(prev => {
        if (!prev) return prev;
        const remainingNodes = (prev.nodes || []).filter(n => n.id !== nodeId);
        const remainingEdges = (prev.edges || []).filter(e => e.source !== nodeId && e.target !== nodeId);
        
        // Add the bridge edges returned by the API
        const newEdges = remainingEdges;
        if (response?.bridge_edges && Array.isArray(response.bridge_edges)) {
          for (const bridgeEdge of response.bridge_edges) {
            newEdges.push({
              id: bridgeEdge.id,
              source: bridgeEdge.source,
              target: bridgeEdge.target,
              relationship: bridgeEdge.relationship,
              confidence: bridgeEdge.confidence,
              status: bridgeEdge.status,
              origin: bridgeEdge.origin,
              created_by: bridgeEdge.created_by,
            });
          }
        }
        
        const remainingGroups = (prev.groups || []).map(g => ({
          ...g,
          nodes: Array.isArray(g.nodes) ? g.nodes.filter(id => id !== nodeId) : [],
        }));
        // Update node_overrides to track that this node is hidden
        const updatedOverrides = { ...prev.node_overrides };
        updatedOverrides[nodeId] = { ...updatedOverrides[nodeId], hidden: true };
        return { ...prev, nodes: remainingNodes, edges: newEdges, groups: remainingGroups, node_overrides: updatedOverrides };
      });

      if ((graph?.nodes?.length ?? 0) > 1) {
        window.setTimeout(() => {
          graphCanvasRef.current?.fitView();
        }, 0);
      }

      setResiliencyEvaluations(prev => {
        if (!prev) return prev;
        const next = { ...prev } as Record<string, any>;
        delete next[nodeId];
        return next;
      });

      setResiliencyData((prev: any) => {
        if (!prev?.evaluations) return prev;
        const nextEvals = { ...prev.evaluations } as Record<string, any>;
        delete nextEvals[nodeId];
        return { ...prev, evaluations: nextEvals };
      });

      setSelectedNode(null);
      setSelectedEdge(null);
      markSubscriptionDirty(nodeSubscriptionId);
    } catch (err) {
      console.error("Failed to hide node", err);
    }
  };

  const handleRestoreAllHiddenResources = async () => {
    if (!graph?.node_overrides || selectedSubscriptionIds.length === 0) return;

    const hiddenNodeIds = Object.entries(graph.node_overrides)
      .filter(([_, override]: [string, any]) => override?.hidden === true)
      .map(([nodeId]) => nodeId);

    if (hiddenNodeIds.length === 0) return;

    try {
      // Helper to extract subscription ID from resource ID
      const extractSubscriptionId = (resourceId: string): string | null => {
        const match = resourceId.match(/\/subscriptions\/([^/]+)/);
        return match ? match[1] : null;
      };

      // Collect all patch operations
      const patchOperations: Array<{nodeId: string; subscriptionId: string}> = [];
      const dirtySubscriptions = new Set<string>();

      hiddenNodeIds.forEach(nodeId => {
        // Try to get subscription ID in order of preference:
        // 1. From node metadata in graph
        const node = graph?.nodes?.find(n => n.id === nodeId);
        let subscriptionId = (node as any)?.subscription_id ?? (node?.metadata as any)?.subscription_id;
        
        // 2. Extract from resource ID itself
        if (!subscriptionId) {
          subscriptionId = extractSubscriptionId(nodeId);
        }
        
        // 3. Use single subscription if available
        if (!subscriptionId && singleSubscriptionId) {
          subscriptionId = singleSubscriptionId;
        }
        
        // 4. Try all selected subscriptions (for edge cases)
        if (!subscriptionId && selectedSubscriptionIds.length > 0) {
          subscriptionId = selectedSubscriptionIds[0];
        }
        
        if (subscriptionId) {
          patchOperations.push({ nodeId, subscriptionId });
          dirtySubscriptions.add(subscriptionId);
        }
      });

      // Clear bridge edges for all affected subscriptions before restoring
      for (const subId of dirtySubscriptions) {
        try {
          await clearBridgeEdges(subId);
        } catch (err) {
          console.warn(`Failed to clear bridge edges for ${subId}:`, err);
        }
      }

      // Execute all patch operations in parallel
      const patchPromises = patchOperations.map(({ nodeId, subscriptionId }) =>
        patchNode(subscriptionId, nodeId, { hidden: false })
      );

      await Promise.all(patchPromises);

      // Mark all affected subscriptions dirty
      dirtySubscriptions.forEach(subId => markSubscriptionDirty(subId));

      // Trigger full refresh to reload graph with restored nodes
      await fetchGraph();
    } catch (err) {
      console.error("Failed to restore hidden resources", err);
      // Silently continue - the refresh might still work
    }
  };;

  const handleRenameNode = async (nodeId: string) => {
    const node = viewGraph?.nodes.find(n => n.id === nodeId);
    if (!node) return;
    setSelectedNode(buildSelectedNodeData(node));
  };

  const handleSaveNode = async (nodeId: string, payload: { name?: string; layer?: number | null; color?: string | null; icon?: string | null; criticality?: number | null }) => {
    try {
      const nodeSubscriptionId = resolveSubscriptionIdForNode(nodeId);
      if (!nodeSubscriptionId) return;

      const nodePatch: Parameters<typeof patchNode>[2] = {
        name: payload.name,
        layer: payload.layer,
        color: payload.color,
        icon: payload.icon,
        criticality_score: payload.criticality,
      };

      await patchNode(nodeSubscriptionId, nodeId, nodePatch);

      // Mark as needing refresh
      markSubscriptionDirty(nodeSubscriptionId);

      updateGraph(prev => {
        if (!prev) return prev;

        const nextOverride: Record<string, unknown> = {};
        if (payload.name !== undefined) nextOverride.name = payload.name ?? undefined;
        if (payload.layer !== undefined) nextOverride.layer = payload.layer ?? undefined;
        if (payload.color !== undefined) nextOverride.color = payload.color ?? undefined;
        if (payload.icon !== undefined) nextOverride.icon = payload.icon ?? undefined;
        if (payload.criticality !== undefined) nextOverride.criticality_score = payload.criticality ?? undefined;

        // Clean undefined values
        const cleanedOverride = Object.fromEntries(
          Object.entries(nextOverride).filter(([, v]) => v !== undefined)
        );

        const nextNodeOverrides = { ...prev.node_overrides };
        if (Object.keys(cleanedOverride).length > 0) nextNodeOverrides[nodeId] = cleanedOverride;
        else delete nextNodeOverrides[nodeId];

        return {
          ...prev,
          node_overrides: nextNodeOverrides,
          nodes: prev.nodes.map(n => {
            if (n.id !== nodeId) return n;
            const meta = (n.metadata as any) ?? {};
            return {
              ...n,
              name: payload.name ?? n.name,
              metadata: {
                ...meta,
                importance: payload.layer === undefined ? meta.importance : payload.layer ?? meta.importance,
                color: payload.color === undefined ? meta.color : payload.color ?? undefined,
                icon: payload.icon === undefined ? meta.icon : payload.icon ?? undefined,
                criticality_score:
                  payload.criticality === undefined
                    ? meta.criticality_score
                    : payload.criticality === null
                      ? meta.criticality_score
                      : payload.criticality,
                user_override: Object.keys(cleanedOverride).length > 0 ? cleanedOverride : undefined,
              }
            };
          })
        };
      });

      setSelectedNode(prev => {
        if (!prev || prev.id !== nodeId) return prev;

        const rawNode = (prev.raw as any) ?? undefined;
        const rawMeta = rawNode?.metadata ?? {};
        const recomputedOverride: Record<string, unknown> = {};
        if (payload.name !== undefined) recomputedOverride.name = payload.name ?? undefined;
        if (payload.layer !== undefined) recomputedOverride.layer = payload.layer ?? undefined;
        if (payload.color !== undefined) recomputedOverride.color = payload.color ?? undefined;
        if (payload.icon !== undefined) recomputedOverride.icon = payload.icon ?? undefined;
        if (payload.criticality !== undefined) recomputedOverride.criticality_score = payload.criticality ?? undefined;

        // Clean undefined values
        const cleanedRecomputedOverride = Object.fromEntries(
          Object.entries(recomputedOverride).filter(([, v]) => v !== undefined)
        );

        const nextRaw = rawNode
          ? {
              ...rawNode,
              name: payload.name ?? rawNode.name,
              metadata: {
                ...rawMeta,
                user_override: Object.keys(cleanedRecomputedOverride).length > 0 ? cleanedRecomputedOverride : undefined,
              },
            }
          : undefined;

        return {
          ...prev,
          name: payload.name ?? prev.name,
          layer: payload.layer === undefined ? prev.layer : payload.layer ?? undefined,
          color: payload.color === undefined ? prev.color : payload.color ?? undefined,
          icon: payload.icon === undefined ? prev.icon : payload.icon ?? undefined,
          override: Object.keys(cleanedRecomputedOverride).length > 0,
          raw: nextRaw ?? prev.raw,
          criticalityScore: payload.criticality === undefined
            ? prev.criticalityScore
            : (payload.criticality === null ? undefined : payload.criticality),
        };
      });
    } catch (err) {
      console.error("Failed to update node", err);
    }
  };

  const applyGroupToNodes = async (args: { groupId: string; label: string; memberIds: string[] }) => {
    const { groupId, label, memberIds } = args;
    if (!memberIds.length) return;

    const groupSubscriptionId = resolveSubscriptionIdForNodeIds(memberIds);
    if (!groupSubscriptionId) return;

    // Recursively find all target dependencies of the selected nodes
    const directDeps = new Set<string>();
    if (graph) {
      const visited = new Set<string>();
      const queue = [...memberIds];
      
      while (queue.length > 0) {
        const nodeId = queue.shift()!;
        if (visited.has(nodeId)) continue;
        visited.add(nodeId);
        
        // Find edges where this node is the source
        const outgoingEdges = graph.edges.filter(e => e.source === nodeId);
        for (const edge of outgoingEdges) {
          // Only include the target if it's not already in memberIds
          if (!memberIds.includes(edge.target) && !visited.has(edge.target)) {
            directDeps.add(edge.target);
            queue.push(edge.target);
          }
        }
      }
    }

    const allMemberIds = [...memberIds, ...Array.from(directDeps)];

    // Optimistic UI update - create new group
    updateGraph(prev => {
      if (!prev) return prev;

      const groups = prev.groups ?? [];
      // Remove nodes from any existing groups
      const updatedGroups = groups.map(g => ({
        ...g,
        nodes: g.nodes.filter(id => !allMemberIds.includes(id)),
      }));
      
      // Add new group
      return {
        ...prev,
        groups: [...updatedGroups, { id: groupId, name: label, nodes: allMemberIds }],
      };
    });

    // Remove nodes from any existing groups first
    if (graph?.groups) {
      for (const group of graph.groups) {
        for (const nodeId of allMemberIds) {
          if (group.nodes.includes(nodeId)) {
            await removeNodeFromGroup(groupSubscriptionId, group.id, nodeId);
          }
        }
      }
    }

    // Create the new group
    await createGroup(groupSubscriptionId, { id: groupId, name: label, nodes: allMemberIds });
  };


  const ungroupNodes = async (args: { groupId: string; memberIds: string[] }) => {
    const { groupId } = args;

    const groupSubscriptionId = resolveSubscriptionIdForNodeIds(args.memberIds);
    if (!groupSubscriptionId) return;

    // Optimistic UI update - remove the group
    updateGraph(prev =>
      prev
        ? {
            ...prev,
            groups: (prev.groups ?? []).filter(g => g.id !== groupId),
          }
        : prev
    );

    // Delete the entire group
    await deleteGroup(groupSubscriptionId, groupId);
    
    // Update groups in state
    updateGraph(prev => prev ? { ...prev, groups: (prev.groups ?? []).filter(g => g.id !== groupId) } : prev);
  };

  const renameGroup = async (args: { groupId: string; label: string; memberIds: string[] }) => {
    const { groupId, label } = args;

    const groupSubscriptionId = resolveSubscriptionIdForNodeIds(args.memberIds);
    if (!groupSubscriptionId) return;

    // Optimistic UI update
    updateGraph(prev =>
      prev
        ? {
            ...prev,
            groups: (prev.groups ?? []).map(g => (g.id === groupId ? { ...g, name: label } : g)),
          }
        : prev
    );

    // Update the group name
    await updateGroup(groupSubscriptionId, groupId, { name: label });
    
    // Update groups in state
    updateGraph(prev => prev ? { ...prev, groups: (prev.groups ?? []).map(g => g.id === groupId ? { ...g, name: label } : g) } : prev);
  };

  const downloadServiceGroupArtifact = (artifact: ServiceGroupArtifact) => {
    const blob = new Blob([artifact.content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = artifact.filename;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  };

  const applyServiceGroupForSelection = async (args: { groupId: string; memberIds: string[] }) => {
    const subscriptionId = resolveSubscriptionIdForNodeIds(args.memberIds);
    if (!subscriptionId) {
      setServiceGroupMessage({ tone: "error", text: "Unable to resolve the subscription for this group." });
      return;
    }

    setServiceGroupBusy(true);
    setServiceGroupMessage(null);
    try {
      const result = await applyServiceGroup(subscriptionId, args.groupId, serviceGroupFormat);
      switch (result.status) {
        case "applied":
          setServiceGroupMessage({
            tone: "success",
            text: `Applied Service Group “${result.display_name}” with ${result.applied_members.length} member${result.applied_members.length === 1 ? "" : "s"}.`,
          });
          break;
        case "permission_denied":
          if (result.artifact) downloadServiceGroupArtifact(result.artifact);
          setServiceGroupMessage({
            tone: "error",
            text: `${result.message ?? "The VM identity lacks permission to write Service Groups."} Downloaded ${result.artifact?.filename ?? "IaC"} so your team can apply it via CI/CD.`,
          });
          break;
        case "empty":
          setServiceGroupMessage({ tone: "info", text: result.message ?? "This group has no Azure resources to include." });
          break;
        default:
          if (result.artifact) downloadServiceGroupArtifact(result.artifact);
          setServiceGroupMessage({
            tone: "error",
            text: `${result.message ?? "Failed to apply the Service Group."}${result.artifact ? ` Downloaded ${result.artifact.filename} as a fallback.` : ""}`,
          });
      }
    } catch (err) {
      setServiceGroupMessage({
        tone: "error",
        text: err instanceof Error ? err.message : "Failed to apply the Service Group.",
      });
    } finally {
      setServiceGroupBusy(false);
    }
  };

  const exportServiceGroupForSelection = async (args: { groupId: string; memberIds: string[] }) => {
    const subscriptionId = resolveSubscriptionIdForNodeIds(args.memberIds);
    if (!subscriptionId) {
      setServiceGroupMessage({ tone: "error", text: "Unable to resolve the subscription for this group." });
      return;
    }

    setServiceGroupBusy(true);
    setServiceGroupMessage(null);
    try {
      const artifact = await exportServiceGroup(subscriptionId, args.groupId, serviceGroupFormat);
      downloadServiceGroupArtifact(artifact);
      setServiceGroupMessage({
        tone: "success",
        text: `Exported ${artifact.filename} (${artifact.member_count} member${artifact.member_count === 1 ? "" : "s"}).`,
      });
    } catch (err) {
      setServiceGroupMessage({
        tone: "error",
        text: err instanceof Error ? err.message : "Failed to export the Service Group.",
      });
    } finally {
      setServiceGroupBusy(false);
    }
  };

  const moveNodeToGroup = async (args: { nodeId: string; groupId: string }) => {
    const { nodeId, groupId } = args;
    if (!graph) return;

    const nodeSubscriptionId = resolveSubscriptionIdForNode(nodeId);
    if (!nodeSubscriptionId) return;

    // Recursively find all target dependencies of the node being moved
    const directDeps = new Set<string>([nodeId]);
    const visited = new Set<string>();
    const queue = [nodeId];
    
    while (queue.length > 0) {
      const currentId = queue.shift()!;
      if (visited.has(currentId)) continue;
      visited.add(currentId);
      
      const outgoingEdges = graph.edges.filter(e => e.source === currentId);
      for (const edge of outgoingEdges) {
        if (!visited.has(edge.target)) {
          directDeps.add(edge.target);
          queue.push(edge.target);
        }
      }
    }

    const allNodeIds = Array.from(directDeps);

    // Remove from any existing groups first
    if (graph.groups) {
      for (const group of graph.groups) {
        for (const id of allNodeIds) {
          if (group.nodes.includes(id) && group.id !== groupId) {
            await removeNodeFromGroup(nodeSubscriptionId, group.id, id);
          }
        }
      }
    }

    // Add all nodes to the group in one API call
    const updatedGroup = await addNodesToGroup(nodeSubscriptionId, groupId, allNodeIds);
    
    // Update just this group in state
    updateGraph(prev => {
      if (!prev) return prev;
      const groups = prev.groups ?? [];
      const updatedGroups = groups.map(g => g.id === updatedGroup.id ? updatedGroup : g);
      return { ...prev, groups: updatedGroups };
    });
  };

  const handleRemoveNodeFromGroup = async (args: { nodeId: string; groupId: string }) => {
    const { nodeId, groupId } = args;
    if (!graph) return;

    const nodeSubscriptionId = resolveSubscriptionIdForNode(nodeId);
    if (!nodeSubscriptionId) return;

    // Remove from the group
    const result = await removeNodeFromGroup(nodeSubscriptionId, groupId, nodeId);
    
    // Update groups in state
    updateGraph(prev => {
      if (!prev) return prev;
      const groups = prev.groups ?? [];
      
      if (result === null) {
        // Group was deleted
        return { ...prev, groups: groups.filter(g => g.id !== groupId) };
      } else {
        // Group was updated
        const updatedGroups = groups.map(g => g.id === result.id ? result : g);
        return { ...prev, groups: updatedGroups };
      }
    });
  };

  const handleNodeRemoveFromGroupClick = (args: { nodeId: string; groupId: string }) => {
    // Just call the handler directly - it's synchronous for UI feedback
    handleRemoveNodeFromGroup(args);
  };

  const handleResetNode = async (nodeId: string) => {
    try {
      const nodeSubscriptionId = resolveSubscriptionIdForNode(nodeId);
      if (!nodeSubscriptionId) return;

      await resetNode(nodeSubscriptionId, nodeId);

      markSubscriptionDirty(nodeSubscriptionId);

      // Remove override from local storage and rebuild graph
      updateGraph(prev => {
        if (!prev) return prev;

        const nextOverrides = { ...prev.node_overrides };
        delete nextOverrides[nodeId];

        return {
          ...prev,
          node_overrides: nextOverrides,
          nodes: prev.nodes.map(n => {
            if (n.id !== nodeId) return n;
            const meta = (n.metadata as any) ?? {};
            const nextMeta = { ...meta };
            delete nextMeta.user_override;
            return {
              ...n,
              metadata: nextMeta,
            };
          }),
        };
      });

      setSelectedNode(null);
    } catch (err) {
      console.error("Failed to reset node", err);
    }
  };

  const handleRefreshAnnotationsAndScores = async () => {
    const subscriptionsToRefresh = Array.from(pendingRefreshSubscriptions);
    if (subscriptionsToRefresh.length === 0) {
      setError("No subscription changes to refresh.");
      return;
    }
    
    try {
      setIsRefreshing(true);
      setError(null);

      const refreshOne = async (subscriptionId: string): Promise<void> => {
        const response = await fetch(
          `/api/subscriptions/${subscriptionId}/refresh`,
          { method: "POST" }
        );

        if (!response.ok) {
          const errorData = await response.json();
          throw new Error(errorData.detail || "Refresh start failed");
        }

        const statusUrl = response.headers.get("Location")
          ?? `/api/subscriptions/${subscriptionId}/refresh/status`;

        const pollStatus = async (): Promise<string> => {
          const s = await fetch(statusUrl);
          if (s.status === 304) return "running";
          if (!s.ok) throw new Error("Status check failed");
          const data = await s.json();
          return data.status || "idle";
        };

        let status = await pollStatus();
        const start = Date.now();
        const timeoutMs = 5 * 60 * 1000; // 5 minutes
        while (status === "running" && Date.now() - start < timeoutMs) {
          await new Promise((r) => setTimeout(r, 2000));
          status = await pollStatus();
        }

        if (status === "failed") {
          throw new Error("LLM refresh failed");
        }
      };

      const results = await Promise.allSettled(
        subscriptionsToRefresh.map(subId => refreshOne(subId))
      );

      const failed = results
        .map((result, index) => ({ result, subscriptionId: subscriptionsToRefresh[index] }))
        .filter(item => item.result.status === "rejected")
        .map(item => item.subscriptionId);

      setPendingRefreshSubscriptions(prev => {
        const next = new Set(prev);
        subscriptionsToRefresh.forEach(subId => {
          if (!failed.includes(subId)) next.delete(subId);
        });
        return next;
      });

      if (failed.length > 0) {
        setError(`Refresh failed for ${failed.length} subscription(s).`);
      }

      // Refresh the graph from server after completion
      await fetchGraph();

      // Refresh chat baseline summary after annotations update
      setChatRefreshToken(prev => prev + 1);

      // Clear the dirty flag if nothing pending
      setNeedsRefresh(failed.length > 0);
    } catch (err: any) {
      console.error("Failed to refresh annotations and scores:", err.message);
      setError(err.message);
    } finally {
      setIsRefreshing(false);
    }
  };

  const runSubscriptionMapping = useCallback(async (
    subscriptionId: string,
    resourceGroups: string[],
    tags: Record<string, string> = {},
  ): Promise<string> => {
    const started = await startSubscriptionMapping(subscriptionId, {
      resource_groups: resourceGroups,
      tags,
    });
    setMappingStatus(started);

    let currentStatus = started.status;
    const startTime = Date.now();
    const timeoutMs = 30 * 60 * 1000;

    while (currentStatus === "running" && Date.now() - startTime < timeoutMs) {
      await new Promise(resolve => setTimeout(resolve, 2000));
      const polled = await fetchSubscriptionMappingStatus(subscriptionId);
      setMappingStatus(polled);
      currentStatus = polled.status;
    }

    return currentStatus;
  }, []);

  const handleStartSubscriptionMapping = useCallback(async (
    payload: {
      subscriptionId: string;
      resourceGroups: string[];
      tags: Record<string, string>;
    }
  ) => {
    if (!payload.subscriptionId || mappingInProgress) return;

    try {
      setMappingError(null);
      setMappingStatus(null);
      setMappingInProgress(true);

      const finalStatus = await runSubscriptionMapping(
        payload.subscriptionId,
        payload.resourceGroups,
        payload.tags,
      );

      if (finalStatus === "failed") {
        throw new Error("Subscription mapping failed");
      }

      if (finalStatus === "completed") {
        await loadMappedSubscriptions(false);
        await loadAvailableSubscriptionsForMapping();
        setSelectedSubscriptions(prev => {
          const next = new Set(prev);
          next.add(payload.subscriptionId);
          return next;
        });
      }
    } catch (err: any) {
      setMappingError(err?.message ?? "Failed to map subscription");
    } finally {
      setMappingInProgress(false);
    }
  }, [mappingInProgress, runSubscriptionMapping, loadAvailableSubscriptionsForMapping, loadMappedSubscriptions]);

  const handleUploadTerraformScripts = useCallback(async (
    payload: { files: File[]; subscriptionName: string }
  ) => {
    try {
      setMappingError(null);
      setMappingAuthRequired(false);
      setMappingStatus(null);
      setMappingInProgress(true);

      const uploaded = await uploadTerraformScripts(payload.files, payload.subscriptionName);

      const subscriptionId = uploaded.subscription_id;
      let polled = await fetchSubscriptionMappingStatus(subscriptionId);
      setMappingStatus(polled);

      let currentStatus = polled.status;
      const startTime = Date.now();
      const timeoutMs = 30 * 60 * 1000;

      while (currentStatus === "running" && Date.now() - startTime < timeoutMs) {
        await new Promise(resolve => setTimeout(resolve, 2000));
        polled = await fetchSubscriptionMappingStatus(subscriptionId);
        setMappingStatus(polled);
        currentStatus = polled.status;
      }

      if (currentStatus === "failed") {
        throw new Error("Terraform mapping failed");
      }

      await loadMappedSubscriptions(false);
      await loadAvailableSubscriptionsForMapping();

      setSelectedSubscriptions(prev => {
        const next = new Set(prev);
        next.add(subscriptionId);
        return next;
      });

      return uploaded;
    } catch (err: any) {
      setMappingError(err?.message ?? "Failed to upload Terraform scripts");
      throw err;
    } finally {
      setMappingInProgress(false);
    }
  }, [loadAvailableSubscriptionsForMapping, loadMappedSubscriptions]);

  // Always respect the view level selection
  const maxImportance = LEVEL_TO_MAX_IMPORTANCE[viewLevel];

  const resetFiltersToAll = useCallback(() => {
    // Reset filters - empty sets will trigger useEffect hooks to select all options
    setServiceFilter(new Set());
    setResourceGroupFilter(new Set());
    setValidationSourceFilter(new Set());
    setExpandedCategories(new Set());
    serviceFilterUserTouchedRef.current = false;
    resourceGroupFilterUserTouchedRef.current = false;
    validationSourceFilterUserTouchedRef.current = false;
  }, []);

  // Reset service filter when view level changes to show all available services at the new level
  useEffect(() => {
    setServiceFilter(new Set());
    serviceFilterUserTouchedRef.current = false;
  }, [viewLevel]);

  const handleSelectedSubscriptionsChange = useCallback((next: Set<string>) => {
    setSelectedSubscriptions(next);
    resetFiltersToAll();
  }, [resetFiltersToAll]);

  const handleWorkloadSelect = useCallback(async (workloadId: string | null) => {
    setWorkloadError(null);
    if (!workloadId) {
      setActiveWorkloadId(null);
      setWorkloadName("");
      setServiceGroupBinding(null);
      setRegionAzCounts({});
      return;
    }
    try {
      const workload = await getWorkload(workloadId);
      const hydratedBinding = await hydrateServiceGroupBinding(workload.view_state.service_group_filter ?? null);
      const nextViewState = hydratedBinding === (workload.view_state.service_group_filter ?? null)
        ? workload.view_state
        : {
            ...workload.view_state,
            service_group_filter: hydratedBinding ?? undefined,
          };
      setActiveWorkloadId(workload.workload_id);
      setWorkloadName(workload.name);
      setActiveWorkloadState(nextViewState);
      applyWorkloadViewState(nextViewState);
    } catch (err: any) {
      setWorkloadError(err.message ?? "Failed to load workload");
    }
  }, [applyWorkloadViewState, hydrateServiceGroupBinding]);

  const currentWorkloadAzureResourceIds = useCallback((): string[] => {
    const seen = new Set<string>();
    const ids: string[] = [];
    for (const node of nodesForView) {
      const id = node.id;
      if (id && id.toLowerCase().startsWith("/subscriptions/") && !seen.has(id)) {
        seen.add(id);
        ids.push(id);
      }
    }
    return ids;
  }, [nodesForView]);

  // A Service Group can only contain live Azure resources in the caller's tenant.
  // Virtual resources (imported from Terraform, or otherwise not backed by a real
  // Azure subscription in this tenant) cannot be attached, so their presence
  // disables Service Group sync for the whole workload.
  const serviceGroupIneligibleMembers = useMemo(() => {
    const seen = new Set<string>();
    const items: Array<{ id: string; name: string }> = [];
    for (const node of nodesForView) {
      const id = node.id;
      if (!id || !id.toLowerCase().startsWith("/subscriptions/")) continue;
      const meta = (node.metadata ?? {}) as Record<string, unknown>;
      if (!meta.virtual) continue;
      if (seen.has(id)) continue;
      seen.add(id);
      items.push({ id, name: (meta.display_name as string) || node.name || id });
    }
    return items;
  }, [nodesForView]);

  const serviceGroupSyncBlocked = serviceGroupIneligibleMembers.length > 0;

  const warnServiceGroupBlocked = useCallback(() => {
    const count = serviceGroupIneligibleMembers.length;
    setServiceGroupMessage({
      tone: "warn",
      text: `Service Group sync is disabled: this workload includes ${count} virtual resource${count === 1 ? "" : "s"} (imported from Terraform or not backed by a live Azure subscription in this tenant) that cannot be added to an Azure Service Group.`,
    });
  }, [serviceGroupIneligibleMembers]);

  // Global gate: the backend identity cannot read Service Groups, so the whole
  // integration (create/import/apply/export) is disabled regardless of the
  // selected workload's contents.
  const warnServiceGroupUnavailable = useCallback(() => {
    setServiceGroupMessage({
      tone: "warn",
      text: serviceGroupUnavailableReason ?? "Service Group integration is unavailable for the backend identity.",
    });
  }, [serviceGroupUnavailableReason]);

  const haveSameWorkloadResourceIds = useCallback((left: string[], right: string[]): boolean => {
    if (left.length !== right.length) return false;
    const normalizedLeft = [...left].map(String).sort();
    const normalizedRight = [...right].map(String).sort();
    return normalizedLeft.every((value, index) => value === normalizedRight[index]);
  }, []);

  const handleWorkloadCreate = useCallback(async () => {
    const name = workloadName.trim();
    if (!name) {
      setWorkloadError("Workload name is required.");
      return;
    }
    try {
      setWorkloadError(null);
      // A newly created workload is never bound to a Service Group. Strip any
      // binding inherited from a previously open workload so it is neither
      // persisted nor synced.
      const view_state = { ...buildWorkloadViewState(), service_group_filter: undefined };
      const created = await createWorkload({ name, view_state });
      setActiveWorkloadId(created.workload_id);
      setActiveWorkloadState(created.view_state);
      setServiceGroupBinding(null);
      await loadWorkloads();
      // "Save as new workload" always offers to create a matching Service Group
      // from the new workload's Azure resources.
      const memberIds = currentWorkloadAzureResourceIds();
      if (!serviceGroupAvailable) {
        warnServiceGroupUnavailable();
      } else if (serviceGroupSyncBlocked) {
        warnServiceGroupBlocked();
      } else if (memberIds.length > 0) {
        setPendingServiceGroupCreate({ memberIds });
      }
    } catch (err: any) {
      setWorkloadError(err.message ?? "Failed to save workload");
    }
  }, [workloadName, buildWorkloadViewState, loadWorkloads, currentWorkloadAzureResourceIds, serviceGroupSyncBlocked, warnServiceGroupBlocked, serviceGroupAvailable, warnServiceGroupUnavailable]);

  const importServiceGroupAsWorkload = useCallback(async (sg: ServiceGroupSummary) => {
    setWorkloadError(null);
    setMappingStatus(null);
    setServiceGroupImport({
      active: true,
      phase: "reading",
      message: `Reading members of '${sg.display_name || sg.name}'…`,
      current: 0,
      total: 0,
    });

    try {
      const memberIds = await fetchServiceGroupMembers(sg.name);

      const memberSubs = new Set<string>();
      const memberRgs = new Set<string>();
      const memberServices = new Set<string>();
      const rgsBySubscription = new Map<string, Set<string>>();
      for (const id of memberIds) {
        const lower = id.toLowerCase();
        const subMatch = lower.match(/\/subscriptions\/([^/]+)/);
        const rgMatch = lower.match(/\/resourcegroups\/([^/]+)/);
        if (subMatch) memberSubs.add(subMatch[1]);
        if (rgMatch) memberRgs.add(rgMatch[1]);
        if (subMatch && rgMatch) {
          if (!rgsBySubscription.has(subMatch[1])) rgsBySubscription.set(subMatch[1], new Set());
          rgsBySubscription.get(subMatch[1])!.add(rgMatch[1]);
        }
        const service = canonicalTypeForResourceId(id);
        if (service && service !== "resource") memberServices.add(service);
      }

      const binding: ServiceGroupBinding = {
        service_group_id: sg.id,
        service_group_name: sg.name,
        display_name: sg.display_name,
        parent_service_group_id: sg.parent_service_group_id ?? undefined,
        member_resource_ids: memberIds,
      };

      const view_state: WorkloadViewState = {
        selected_subscriptions: Array.from(memberSubs),
        view_level: "full",
        ai_layer_enabled: true,
        user_layer_enabled: true,
        resource_group_filter: Array.from(memberRgs),
        service_filter: Array.from(memberServices),
        expanded_categories: [],
        show_legend: false,
        service_group_filter: binding,
      };

      setServiceGroupImport({
        active: true,
        phase: "creating",
        message: "Creating workload…",
        current: 0,
        total: 0,
      });
      const created = await createWorkload({ name: sg.display_name || sg.name, view_state });
      await loadWorkloads();

      // Map any member subscriptions that have not been collected yet so the
      // imported workload renders full edges, annotations, and resilience.
      const mappedIds = new Set(subscriptions.map(s => s.id));
      const toMap = Array.from(memberSubs).filter(id => !mappedIds.has(id));

      if (toMap.length > 0) {
        setMappingInProgress(true);
        try {
          let index = 0;
          for (const subId of toMap) {
            index += 1;
            setServiceGroupImport({
              active: true,
              phase: "mapping",
              message: `Mapping subscription ${index} of ${toMap.length}…`,
              current: index,
              total: toMap.length,
            });
            const resourceGroups = Array.from(rgsBySubscription.get(subId) ?? []);
            const status = await runSubscriptionMapping(subId, resourceGroups);
            if (status === "failed") {
              throw new Error(`Mapping failed for subscription ${subId}.`);
            }
          }
          await loadMappedSubscriptions(false);
          await loadAvailableSubscriptionsForMapping();
        } finally {
          setMappingInProgress(false);
        }
      }

      setServiceGroupImport({
        active: false,
        phase: "completed",
        message:
          toMap.length > 0
            ? `Imported '${sg.display_name || sg.name}' and mapped ${toMap.length} subscription(s).`
            : `Imported '${sg.display_name || sg.name}'.`,
        current: toMap.length,
        total: toMap.length,
      });
      await handleWorkloadSelect(created.workload_id);
    } catch (err: any) {
      setServiceGroupImport({
        active: false,
        phase: "error",
        message: err?.message ?? "Failed to import Service Group.",
        current: 0,
        total: 0,
      });
      setWorkloadError(err?.message ?? "Failed to import Service Group.");
      throw err;
    }
  }, [subscriptions, runSubscriptionMapping, loadWorkloads, loadMappedSubscriptions, loadAvailableSubscriptionsForMapping, handleWorkloadSelect]);

  const syncWorkloadServiceGroup = useCallback(
    async (opts: {
      workloadId: string;
      displayName: string;
      memberIds: string[];
      binding: ServiceGroupBinding | null;
      parentServiceGroupId?: string | null;
    }): Promise<void> => {
      const { workloadId, displayName, memberIds, binding, parentServiceGroupId } = opts;
      if (!serviceGroupAvailable) {
        warnServiceGroupUnavailable();
        return;
      }
      if (serviceGroupSyncBlocked) {
        warnServiceGroupBlocked();
        return;
      }
      setServiceGroupBusy(true);
      setServiceGroupMessage(null);
      try {
        const result = await applyServiceGroupFromWorkload({
          workload_id: workloadId,
          display_name: displayName,
          member_resource_ids: memberIds,
          previous_member_resource_ids: binding?.member_resource_ids ?? [],
          service_group_name: binding?.service_group_name,
          parent_service_group_id: parentServiceGroupId ?? binding?.parent_service_group_id ?? null,
          fallback_format: serviceGroupFormat,
        });

        // A Service Group id + at least one attached member proves the Service
        // Group now exists in Azure. Binding to it (and persisting it in the
        // workload's view_state) is what lets a later Save UPDATE the existing
        // Service Group instead of re-offering to create a duplicate — this is
        // essential for partial results, where some members failed transiently.
        const bindAndPersist = async (nextMemberIds: string[]): Promise<ServiceGroupBinding> => {
          const nextBinding: ServiceGroupBinding = {
            service_group_id: result.service_group_id ?? binding?.service_group_id,
            service_group_name: result.service_group_name ?? binding?.service_group_name,
            display_name: result.display_name ?? displayName,
            parent_service_group_id:
              result.parent_service_group_id ?? binding?.parent_service_group_id ?? parentServiceGroupId ?? undefined,
            member_resource_ids: nextMemberIds,
          };
          setServiceGroupBinding(nextBinding);
          const updated = await updateWorkload(workloadId, {
            view_state: { ...buildWorkloadViewState(), service_group_filter: nextBinding },
          });
          setActiveWorkloadState(updated.view_state);
          return nextBinding;
        };

        if (result.status === "applied") {
          const nextBinding = await bindAndPersist(memberIds);
          const detached = result.detached_members?.length ?? 0;
          const attached = result.applied_members.length;
          setServiceGroupMessage({
            tone: "success",
            text: `Service Group “${nextBinding.display_name}” saved: ${attached} member${attached === 1 ? "" : "s"} attached${detached ? `, ${detached} removed` : ""}.`,
          });
        } else if (result.status === "permission_denied") {
          if (result.artifact) downloadServiceGroupArtifact(result.artifact);
          setServiceGroupMessage({
            tone: "error",
            text: `${result.message ?? "The backend identity lacks permission to write Service Groups."}${result.artifact ? ` Downloaded ${result.artifact.filename} so your team can apply it via CI/CD.` : ""}`,
          });
        } else if (result.status === "empty") {
          setServiceGroupMessage({
            tone: "info",
            text: result.message ?? "This workload has no Azure resources to include.",
          });
        } else {
          // Partial or genuine failure. If the Service Group was created and at
          // least one member attached, bind to it and persist so the NEXT Save
          // UPDATES the existing Service Group (attaching the stragglers) rather
          // than offering to create a duplicate. Then surface the partial result.
          const attached = result.applied_members?.length ?? 0;
          const failedCount = result.failed_members?.length ?? 0;
          const detachedMembers = new Set(result.detached_members ?? []);
          const previousMembers = new Set(binding?.member_resource_ids ?? []);
          const nextMemberIds = [
            ...Array.from(previousMembers).filter(id => !detachedMembers.has(id)),
            ...result.applied_members.filter(id => !previousMembers.has(id)),
          ];
          if (attached > 0 || detachedMembers.size > 0) {
            await bindAndPersist(nextMemberIds);
          }
          setServiceGroupMessage({
            tone: attached > 0 ? "info" : "error",
            text:
              attached > 0
                ? `Service Group partially saved: ${attached} member${attached === 1 ? "" : "s"} attached, ${failedCount} still pending. ${result.message ?? ""} Save again to attach the remaining resource${failedCount === 1 ? "" : "s"}.`.trim()
                : `${result.message ?? "Failed to save the Service Group."}${failedCount ? ` (${failedCount} resource${failedCount === 1 ? "" : "s"} affected)` : ""}`,
          });
        }
      } catch (err) {
        setServiceGroupMessage({
          tone: "error",
          text: err instanceof Error ? err.message : "Failed to save the Service Group.",
        });
      } finally {
        setServiceGroupBusy(false);
      }
    },
    [serviceGroupFormat, buildWorkloadViewState, serviceGroupSyncBlocked, warnServiceGroupBlocked, serviceGroupAvailable, warnServiceGroupUnavailable]
  );

  const handleWorkloadSave = useCallback(async () => {
    if (!activeWorkloadId) return;
    try {
      setWorkloadError(null);
      const view_state = buildWorkloadViewState();
      const updated = await updateWorkload(activeWorkloadId, { view_state });
      setActiveWorkloadState(updated.view_state);
      await loadWorkloads();

      const memberIds = currentWorkloadAzureResourceIds();
      if (!serviceGroupAvailable) {
        // Workload metadata is still saved; only Service Group sync is disabled.
        warnServiceGroupUnavailable();
      } else if (serviceGroupSyncBlocked) {
        // Workload metadata is still saved; only Service Group sync is disabled.
        warnServiceGroupBlocked();
      } else if (serviceGroupBinding) {
        const previousMemberIds = serviceGroupBinding.member_resource_ids ?? [];
        if (!haveSameWorkloadResourceIds(memberIds, previousMemberIds)) {
          // Only sync the Service Group when the resource membership actually changed.
          await syncWorkloadServiceGroup({
            workloadId: activeWorkloadId,
            displayName: serviceGroupBinding.display_name || workloadName,
            memberIds,
            binding: serviceGroupBinding,
          });
        }
      } else if (memberIds.length > 0) {
        // Not bound yet → ask whether to create a Service Group.
        setPendingServiceGroupCreate({ memberIds });
      }
    } catch (err: any) {
      setWorkloadError(err.message ?? "Failed to update workload");
    }
  }, [
    activeWorkloadId,
    buildWorkloadViewState,
    loadWorkloads,
    currentWorkloadAzureResourceIds,
    haveSameWorkloadResourceIds,
    serviceGroupBinding,
    syncWorkloadServiceGroup,
    workloadName,
    serviceGroupSyncBlocked,
    warnServiceGroupBlocked,
    serviceGroupAvailable,
    warnServiceGroupUnavailable,
  ]);

  const confirmCreateServiceGroup = useCallback(async () => {
    if (!activeWorkloadId || !pendingServiceGroupCreate) return;
    const { memberIds } = pendingServiceGroupCreate;
    const parentServiceGroupId = selectedParentServiceGroupId || null;
    setPendingServiceGroupCreate(null);
    await syncWorkloadServiceGroup({
      workloadId: activeWorkloadId,
      displayName: workloadName,
      memberIds,
      binding: null,
      parentServiceGroupId,
    });
  }, [
    activeWorkloadId,
    pendingServiceGroupCreate,
    selectedParentServiceGroupId,
    syncWorkloadServiceGroup,
    workloadName,
  ]);

  // Load selectable parents (existing Service Groups) when the create modal opens.
  useEffect(() => {
    if (!pendingServiceGroupCreate) return;
    let cancelled = false;
    setSelectedParentServiceGroupId("");
    listAzureServiceGroups()
      .then((groups) => {
        if (!cancelled) setParentServiceGroupOptions(groups);
      })
      .catch(() => {
        if (!cancelled) setParentServiceGroupOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [pendingServiceGroupCreate]);


  const handleWorkloadRename = useCallback(async () => {
    if (!activeWorkloadId) return;
    const name = workloadName.trim();
    if (!name) {
      setWorkloadError("Workload name is required.");
      return;
    }
    try {
      setWorkloadError(null);
      const updated = await updateWorkload(activeWorkloadId, { name });
      setWorkloadName(updated.name);

       // Keep the linked Azure Service Group display name aligned with the
       // workload name when this workload is bound to a Service Group.
       if (serviceGroupBinding) {
         await syncWorkloadServiceGroup({
           workloadId: activeWorkloadId,
           displayName: updated.name,
           memberIds: currentWorkloadAzureResourceIds(),
           binding: serviceGroupBinding,
         });
       }

      await loadWorkloads();
    } catch (err: any) {
      setWorkloadError(err.message ?? "Failed to rename workload");
    }
  }, [
    activeWorkloadId,
    workloadName,
    serviceGroupBinding,
    syncWorkloadServiceGroup,
    currentWorkloadAzureResourceIds,
    loadWorkloads,
  ]);

  const performWorkloadDelete = useCallback(
    async (workloadId: string, serviceGroupName: string | null): Promise<void> => {
      try {
        setWorkloadError(null);
        // Optionally delete the backing Azure Service Group first. If that fails,
        // abort so the local workload (and its binding) is preserved and the user
        // can retry or choose "workload only".
        if (serviceGroupName) {
          setServiceGroupDeleting(true);
          setServiceGroupMessage(null);
          try {
            const result = await deleteServiceGroup(serviceGroupName);
            if (result.status !== "deleted") {
              setServiceGroupMessage({
                tone: result.status === "empty" ? "info" : "error",
                text: result.message ?? "Failed to delete the Service Group.",
              });
              return;
            }
            setServiceGroupMessage({
              tone: "success",
              text: result.message ?? "Service Group deleted.",
            });
          } finally {
            setServiceGroupDeleting(false);
          }
        }

        await deleteWorkload(workloadId);
        setActiveWorkloadId(null);
        setActiveWorkloadState(null);
        setWorkloadName("");
        setServiceGroupBinding(null);
        await loadWorkloads();
      } catch (err: any) {
        setWorkloadError(err.message ?? "Failed to delete workload");
      }
    },
    [loadWorkloads]
  );

  const handleWorkloadDelete = useCallback(async () => {
    if (!activeWorkloadId) return;
    // A workload bound to an Azure Service Group triggers a confirmation asking
    // whether to also delete that Service Group; unbound workloads delete directly.
    if (serviceGroupBinding?.service_group_name) {
      setPendingWorkloadDelete({
        workloadId: activeWorkloadId,
        workloadName,
        serviceGroupName: serviceGroupBinding.service_group_name,
        serviceGroupDisplayName:
          serviceGroupBinding.display_name || serviceGroupBinding.service_group_name,
      });
      return;
    }
    await performWorkloadDelete(activeWorkloadId, null);
  }, [activeWorkloadId, serviceGroupBinding, workloadName, performWorkloadDelete]);

  useEffect(() => {
    if (activeSubscriptionId && selectedSubscriptionIds.includes(activeSubscriptionId)) return;
    if (singleSubscriptionId) {
      setActiveSubscriptionId(singleSubscriptionId);
      return;
    }
    setActiveSubscriptionId(null);
  }, [activeSubscriptionId, selectedSubscriptionIds, singleSubscriptionId]);

  const handleMouseDown = (e: React.MouseEvent) => {
    setIsResizing(true);
    e.preventDefault();
  };

  const handleMouseMove = useCallback((e: MouseEvent) => {
    if (!isResizing) return;
    const newWidth = Math.max(250, Math.min(600, e.clientX));
    setSidebarWidth(newWidth);
  }, [isResizing]);

  const handleMouseUp = useCallback(() => {
    setIsResizing(false);
  }, []);

  useEffect(() => {
    if (isResizing) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
      return () => {
        document.removeEventListener('mousemove', handleMouseMove);
        document.removeEventListener('mouseup', handleMouseUp);
      };
    }
  }, [isResizing, handleMouseMove, handleMouseUp]);


  if (loading) {
    return (
      <div style={{ padding: 16, color: "#ccc" }}>
        Loading workload graph…
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: 16, color: "#f44336" }}>
        {error}
      </div>
    );
  }

  // Service Group apply/export controls are disabled while a mutation is in
  // flight OR when the backend identity can't read Service Groups at all.
  const serviceGroupControlsDisabled = serviceGroupBusy || !serviceGroupAvailable;

  const suggestGroupName = (selectedIds: string[]): string => {
    if (selectedIds.length === 0) return "";

    const degreeById = new Map<string, number>();
    for (const e of edgesForView) {
      degreeById.set(e.source, (degreeById.get(e.source) ?? 0) + 1);
      degreeById.set(e.target, (degreeById.get(e.target) ?? 0) + 1);
    }

    const candidates = selectedIds
      .map(id => nodesForView.find(n => n.id === id))
      .filter((n): n is (typeof nodesForView)[number] => !!n);

    if (candidates.length === 0) return "";

    const score = (n: (typeof nodesForView)[number]): number => {
      const meta: any = (n as any).metadata ?? {};
      const v = meta.criticality_score;
      return typeof v === "number" ? v : 0;
    };

    const degree = (n: (typeof nodesForView)[number]): number => degreeById.get(n.id) ?? 0;

    const name = (n: (typeof nodesForView)[number]): string => {
      const raw = (n as any).name as string | undefined;
      if (raw && raw.trim()) return raw.trim();
      const last = n.id.split("/").pop();
      return (last && last.trim()) ? last.trim() : n.id;
    };

    const best = [...candidates].sort((a, b) => {
      const s = score(b) - score(a);
      if (s !== 0) return s;

      const d = degree(b) - degree(a);
      if (d !== 0) return d;

      return name(a).localeCompare(name(b));
    })[0];

    return name(best);
  };

  // A Service Group mutation (save/sync/create OR delete) is in flight; drives
  // the status toast. The Save button uses serviceGroupBusy alone so a delete
  // does not spin it.
  const serviceGroupInFlight = serviceGroupBusy || serviceGroupDeleting;

  return (
    <div
      style={{
        display: "flex",
        height: "100vh",
        width: "100%",
        overflow: "hidden",
        fontFamily: "Segoe UI, Tahoma, Geneva, Verdana, sans-serif",
      }}
    >
      {/* Global Service Group status toast: always visible so the user has
          feedback during the slow, serial Azure ARM sync and can read the
          success/error outcome regardless of which panel is open. */}
      {(serviceGroupInFlight || serviceGroupMessage) && (
        <div
          role="status"
          aria-live="polite"
          style={{
            position: "fixed",
            bottom: 20,
            right: 20,
            zIndex: 1000,
            maxWidth: 360,
            display: "flex",
            alignItems: "flex-start",
            gap: 8,
            padding: "10px 12px",
            borderRadius: 4,
            fontSize: 13,
            lineHeight: 1.4,
            boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
            border: "1px solid",
            borderColor: serviceGroupInFlight
              ? "#c8c6c4"
              : serviceGroupMessage?.tone === "success"
              ? "#a7d8a7"
              : serviceGroupMessage?.tone === "error"
              ? "#e6a3a6"
              : serviceGroupMessage?.tone === "warn"
              ? "#e6c07b"
              : "#c8c6c4",
            background: serviceGroupInFlight
              ? "#faf9f8"
              : serviceGroupMessage?.tone === "success"
              ? "#f1faf1"
              : serviceGroupMessage?.tone === "error"
              ? "#fdf3f4"
              : serviceGroupMessage?.tone === "warn"
              ? "#fdf6e3"
              : "#faf9f8",
            color: serviceGroupInFlight
              ? "#323130"
              : serviceGroupMessage?.tone === "success"
              ? "#107c10"
              : serviceGroupMessage?.tone === "error"
              ? "#a4262c"
              : serviceGroupMessage?.tone === "warn"
              ? "#8a6116"
              : "#605e5c",
          }}
        >
          {serviceGroupInFlight ? (
            <>
              <ArrowSync16Regular className="wl-spin" style={{ flexShrink: 0, marginTop: 1 }} />
              <span>Updating the Service Group in Azure… This can take a few seconds per resource.</span>
            </>
          ) : (
            <>
              <span style={{ flex: 1 }}>{serviceGroupMessage?.text}</span>
              <button
                onClick={() => setServiceGroupMessage(null)}
                title="Dismiss"
                style={{
                  flexShrink: 0,
                  border: "none",
                  background: "transparent",
                  cursor: "pointer",
                  color: "inherit",
                  padding: 2,
                  display: "flex",
                  alignItems: "center",
                }}
              >
                <Dismiss12Regular />
              </button>
            </>
          )}
        </div>
      )}

      {/* Left sidebar */}
      <div
        style={{
          width: sidebarOpen ? sidebarWidth : 0,
          minWidth: sidebarOpen ? sidebarWidth : 0,
          background: "#f5f5f5",
          borderRight: sidebarOpen ? "1px solid #222" : "none",
          transition: isResizing ? "none" : "width 0.3s ease, min-width 0.3s ease",
          overflow: "hidden",
          display: "flex",
          flexDirection: "row",
          flexShrink: 0,
          position: "relative"
        }}
      >
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden"
          }}
        >
          <WorkloadSidebar
            subscriptions={subscriptions.map(sub => ({ id: sub.id, name: sub.name }))}
            selectedSubscriptions={selectedSubscriptions}
            onSelectedSubscriptionsChange={handleSelectedSubscriptionsChange}
            workloads={workloads}
            activeWorkloadId={activeWorkloadId}
            workloadName={workloadName}
            onWorkloadNameChange={setWorkloadName}
            onWorkloadSelect={handleWorkloadSelect}
            onWorkloadCreate={handleWorkloadCreate}
            onWorkloadSave={handleWorkloadSave}
            onWorkloadRename={handleWorkloadRename}
            onWorkloadDelete={handleWorkloadDelete}
            onListServiceGroups={listAzureServiceGroups}
            onImportServiceGroup={importServiceGroupAsWorkload}
            serviceGroupBusy={serviceGroupBusy}
            serviceGroupImportStatus={serviceGroupImport}
            serviceGroupAvailable={serviceGroupAvailable}
            serviceGroupUnavailableReason={serviceGroupUnavailableReason}
            workloadError={workloadError}
            workloadDirty={isWorkloadDirty}
            workloadNewDirty={isNewWorkloadDirty}
            workloadRegions={workloadRegions}
            regionAzCounts={regionAzCounts}
            onRegionAzCountChange={handleRegionAzCountChange}
            viewLevel={viewLevel}
            onViewLevelChange={setViewLevel}
            resourceGroupOptions={resourceGroupOptions}
            resourceGroupFilter={resourceGroupFilter}
            onResourceGroupFilterChange={handleResourceGroupFilterChange}
            serviceOptions={serviceOptions}
            serviceFilter={serviceFilter}
            onServiceFilterChange={handleServiceFilterChange}
            validationSourceOptions={validationSourceOptions}
            validationSourceFilter={validationSourceFilter}
            onValidationSourceFilterChange={handleValidationSourceFilterChange}
            expandedCategories={expandedCategories}
            onExpandedCategoriesChange={setExpandedCategories}
            showLegend={showLegend}
            onToggleLegend={() => setShowLegend(prev => !prev)}
            availableSubscriptionsForMapping={availableSubscriptionsForMapping}
            onRefreshAvailableSubscriptions={() => {
              loadAvailableSubscriptionsForMapping().catch(err => {
                setAvailableSubscriptionsForMapping([]);
                setMappingError(err?.message ?? "Failed to discover subscriptions");
                setMappingAuthRequired(err?.code === "AZURE_AUTH_REQUIRED");
              });
            }}
            onDiscoverMappingResourceGroups={discoverSubscriptionResourceGroups}
            onStartSubscriptionMapping={handleStartSubscriptionMapping}
            onUploadTerraformScripts={handleUploadTerraformScripts}
            mappingInProgress={mappingInProgress}
            mappingStatus={mappingStatus}
            mappingError={mappingError}
            mappingAuthRequired={mappingAuthRequired}
          />

          {/* Group toolbar (shows only for multi-select or selected group) */}
          {(() => {
            const hasMultiSelect = groupToolbarSelection.selectedGroupId === null && groupToolbarSelection.selectedNodeIds.length > 1;
            const hasGroupSelected = !!groupToolbarSelection.selectedGroupId;
            if (!hasMultiSelect && !hasGroupSelected) return null;

            const isSaveDisabled = groupToolbarName.trim().length === 0;

            return (
              <div
                style={{
                  padding: "10px 12px 12px 12px",
                  background: "#fff",
                  borderTop: "1px solid #e0e0e0",
                  borderBottom: "1px solid #e0e0e0",
                  display: "flex",
                  flexDirection: "column",
                  gap: 8,
                  color: "#323130",
                  flexShrink: 0,
                }}
              >
                <div style={{ fontSize: 12, color: "#605e5c", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px" }}>Group</div>

                <input
                  value={groupToolbarName}
                  onChange={e => setGroupToolbarName(e.target.value)}
                  placeholder={hasMultiSelect ? "Enter group name" : "Group name"}
                  style={{
                    padding: "5px 8px",
                    background: "#fff",
                    color: "#323130",
                    border: "1px solid #8a8886",
                    borderRadius: 2,
                    fontSize: 13,
                    outline: "none",
                  }}
                  onFocus={e => e.target.style.borderColor = "#0078d4"}
                  onBlur={e => e.target.style.borderColor = "#8a8886"}
                />

                <div style={{ display: "flex", gap: 8 }}>
                  <button
                    disabled={isSaveDisabled}
                    onClick={async () => {
                      const label = groupToolbarName.trim();
                      if (!label) return;

                      if (hasMultiSelect) {
                        setGroupCreateRequest({ nonce: Date.now(), label });
                        setGroupToolbarName("");
                        lastSuggestedGroupNameRef.current = "";
                      } else if (hasGroupSelected) {
                        const gid = groupToolbarSelection.selectedGroupId!;
                        const members = groupToolbarSelection.selectedGroupMemberIds ?? [];
                        if (members.length) await renameGroup({ groupId: gid, label, memberIds: members });
                      }
                    }}
                    style={{
                      flex: 1,
                      padding: "6px 12px",
                      background: isSaveDisabled ? "#f3f2f1" : "#0078d4",
                      color: isSaveDisabled ? "#a19f9d" : "#fff",
                      border: isSaveDisabled ? "1px solid #c8c6c4" : "1px solid #0078d4",
                      borderRadius: 2,
                      cursor: isSaveDisabled ? "not-allowed" : "pointer",
                      fontSize: 13,
                      fontWeight: 400,
                      transition: "all 0.1s ease-in-out",
                    }}
                    onMouseEnter={e => {
                      if (!isSaveDisabled) e.currentTarget.style.background = "#106ebe";
                    }}
                    onMouseLeave={e => {
                      if (!isSaveDisabled) e.currentTarget.style.background = "#0078d4";
                    }}
                  >
                    Save
                  </button>

                  {hasGroupSelected && (
                    <button
                      onClick={async () => {
                        const gid = groupToolbarSelection.selectedGroupId!;
                        const members = groupToolbarSelection.selectedGroupMemberIds ?? [];
                        if (members.length) await ungroupNodes({ groupId: gid, memberIds: members });
                        setGroupToolbarName("");
                      }}
                      style={{
                        flex: 1,
                        padding: "6px 12px",
                        background: "transparent",
                        color: "#0078d4",
                        border: "1px solid #8a8886",
                        borderRadius: 2,
                        cursor: "pointer",
                        fontSize: 13,
                        fontWeight: 400,
                        transition: "all 0.1s ease-in-out",
                      }}
                      onMouseEnter={e => {
                        e.currentTarget.style.background = "rgba(0, 120, 212, 0.05)";
                        e.currentTarget.style.borderColor = "#0078d4";
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.background = "transparent";
                        e.currentTarget.style.borderColor = "#8a8886";
                      }}
                    >
                      Ungroup
                    </button>
                  )}
                </div>

                {hasGroupSelected && (
                  <div
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: 8,
                      paddingTop: 8,
                      borderTop: "1px dashed #e0e0e0",
                    }}
                  >
                    <div style={{ fontSize: 12, color: "#605e5c", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px" }}>
                      Azure Service Group
                    </div>

                    {!serviceGroupAvailable && (
                      <div style={{ fontSize: 12, color: "#8a6116", background: "#fdf6e3", border: "1px solid #e6c07b", borderRadius: 2, padding: "6px 8px" }}>
                        {serviceGroupUnavailableReason ?? "Service Group integration is unavailable for the backend identity."}
                      </div>
                    )}

                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <label style={{ fontSize: 12, color: "#605e5c" }} htmlFor="sg-format">Format</label>
                      <select
                        id="sg-format"
                        value={serviceGroupFormat}
                        onChange={e => setServiceGroupFormat(e.target.value as ServiceGroupFormat)}
                        disabled={serviceGroupControlsDisabled}
                        style={{
                          flex: 1,
                          padding: "5px 8px",
                          background: "#fff",
                          color: "#323130",
                          border: "1px solid #8a8886",
                          borderRadius: 2,
                          fontSize: 13,
                          outline: "none",
                        }}
                      >
                        <option value="terraform">Terraform (azapi)</option>
                        <option value="arm">ARM template</option>
                      </select>
                    </div>

                    <div style={{ display: "flex", gap: 8 }}>
                      <button
                        disabled={serviceGroupControlsDisabled}
                        onClick={() => {
                          const gid = groupToolbarSelection.selectedGroupId!;
                          const members = groupToolbarSelection.selectedGroupMemberIds ?? [];
                          void applyServiceGroupForSelection({ groupId: gid, memberIds: members });
                        }}
                        title="Create the Service Group in Azure using the VM's managed identity"
                        style={{
                          flex: 1,
                          padding: "6px 12px",
                          background: serviceGroupControlsDisabled ? "#f3f2f1" : "#0078d4",
                          color: serviceGroupControlsDisabled ? "#a19f9d" : "#fff",
                          border: serviceGroupControlsDisabled ? "1px solid #c8c6c4" : "1px solid #0078d4",
                          borderRadius: 2,
                          cursor: serviceGroupControlsDisabled ? "not-allowed" : "pointer",
                          fontSize: 13,
                        }}
                      >
                        Apply to Azure
                      </button>

                      <button
                        disabled={serviceGroupControlsDisabled}
                        onClick={() => {
                          const gid = groupToolbarSelection.selectedGroupId!;
                          const members = groupToolbarSelection.selectedGroupMemberIds ?? [];
                          void exportServiceGroupForSelection({ groupId: gid, memberIds: members });
                        }}
                        title="Download infrastructure-as-code to share with your team or CI/CD"
                        style={{
                          flex: 1,
                          padding: "6px 12px",
                          background: "transparent",
                          color: serviceGroupControlsDisabled ? "#a19f9d" : "#0078d4",
                          border: "1px solid #8a8886",
                          borderRadius: 2,
                          cursor: serviceGroupControlsDisabled ? "not-allowed" : "pointer",
                          fontSize: 13,
                        }}
                      >
                        Export IaC
                      </button>
                    </div>
                  </div>
                )}

                {hasMultiSelect && (
                  <button
                    onClick={async () => {
                      const nodeIds = groupToolbarSelection.selectedNodeIds;
                      for (const nodeId of nodeIds) {
                        await handleHideNode(nodeId);
                      }
                    }}
                    style={{
                      width: "100%",
                      padding: "6px 12px",
                      background: "transparent",
                      color: "#a4262c",
                      border: "1px solid #8a8886",
                      borderRadius: 2,
                      cursor: "pointer",
                      fontSize: 13,
                      fontWeight: 400,
                      transition: "all 0.1s ease-in-out",
                    }}
                    onMouseEnter={e => {
                      e.currentTarget.style.background = "rgba(164, 38, 44, 0.05)";
                      e.currentTarget.style.borderColor = "#a4262c";
                    }}
                    onMouseLeave={e => {
                      e.currentTarget.style.background = "transparent";
                      e.currentTarget.style.borderColor = "#8a8886";
                    }}
                    title={`Hide ${groupToolbarSelection.selectedNodeIds.length} selected resource${groupToolbarSelection.selectedNodeIds.length > 1 ? "s" : ""}`}
                  >
                    ✕ Hide All ({groupToolbarSelection.selectedNodeIds.length})
                  </button>
                )}
              </div>
            );
          })()}

          {/* Restore hidden resources */}
          {hiddenResourcesCount > 0 && (
            <div
              style={{
                padding: "2px 12px",
                flexShrink: 0,
              }}
            >
              <button
                onClick={handleRestoreAllHiddenResources}
                style={{
                  width: "100%",
                  padding: "6px 12px",
                  background: "transparent",
                  color: "#0078d4",
                  border: "1px solid #8a8886",
                  borderRadius: 2,
                  cursor: "pointer",
                  fontSize: 13,
                  fontWeight: 400,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 6,
                  transition: "all 0.1s ease-in-out",
                }}
                onMouseEnter={e => {
                  e.currentTarget.style.background = "rgba(0, 120, 212, 0.05)";
                  e.currentTarget.style.borderColor = "#0078d4";
                }}
                onMouseLeave={e => {
                  e.currentTarget.style.background = "transparent";
                  e.currentTarget.style.borderColor = "#8a8886";
                }}
              >
                <span>↺</span>
                <span>Restore {hiddenResourcesCount} hidden resource{hiddenResourcesCount > 1 ? "s" : ""}</span>
              </button>
            </div>
          )}
        </div>

        {/* Resize handle */}
        {sidebarOpen && (
          <div
            onMouseDown={handleMouseDown}
            style={{
              width: 5,
              cursor: "ew-resize",
              background: isResizing ? "#85a2c6ff" : "transparent",
              transition: "background 0.2s",
              position: "relative",
              flexShrink: 0
            }}
            onMouseEnter={(e) => {
              if (!isResizing) {
                e.currentTarget.style.background = "#333";
              }
            }}
            onMouseLeave={(e) => {
              if (!isResizing) {
                e.currentTarget.style.background = "transparent";
              }
            }}
          />
        )}
      </div>

      {/* Main content area */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
        {/* Top bar with toggle */}
        <div
          style={{
            padding: "10px 14px",
            background: "#f5f5f5",
            borderBottom: "1px solid #e0e0e0",
            display: "flex",
            gap: 12,
            alignItems: "center",
            color: "#323130",
            flexWrap: "wrap",
          }}
        >
          <button
            onClick={() => setSidebarOpen(prev => !prev)}
            style={{
              border: "0px",
              cursor: "pointer",
              fontSize: 13,
              transition: "all 0.1s ease-in-out",
              background: "transparent",
            }}
            title="Toggle sidebar"
            onMouseEnter={e => {
              e.currentTarget.style.background = "#ebf4fc";
              e.currentTarget.style.borderColor = "#0078d4";
            }}
            onMouseLeave={e => {
              e.currentTarget.style.background = "transparent";
              e.currentTarget.style.borderColor = "#8a8886";
            }}
          >
            {sidebarOpen ? <ArrowCollapseAll16Regular style={{ fontSize: 16, rotate: "-90deg" }} /> : <ArrowExpandAll16Regular style={{ fontSize: 16, rotate: "-90deg" }} />}
          </button>
          <h2 style={{ margin: 0, fontSize: 16, color: "#323130", flex: "1 1 220px", minWidth: 180 }}>Azure Resiliency IQ</h2>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end" }}>
            {pdfReport && (
              <button
                onClick={async () => {
                  setIsExportingPdf(true);
                  try {
                    await handleExportPdf(pdfReport);
                  } catch (exportError) {
                    console.error("Failed to export resilience PDF:", exportError);
                    alert("Failed to export the PDF report. Please try again.");
                  } finally {
                    setIsExportingPdf(false);
                  }
                }}
                disabled={isExportingPdf}
                title="Download evaluation report as PDF"
                style={{
                  width: 132,
                  height: 34,
                  padding: "6px 12px",
                  background: "#0078d4",
                  color: "#fff",
                  border: "1px solid #0078d4",
                  borderRadius: 4,
                  cursor: isExportingPdf ? "wait" : "pointer",
                  fontSize: 12,
                  fontWeight: 600,
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  opacity: isExportingPdf ? 0.7 : 1,
                }}
              >
                <DocumentPdf20Regular />
                {isExportingPdf ? "Creating PDF..." : "Export PDF"}
              </button>
            )}
            <button
              onClick={() => {
                fetchGraph();
                fetchZonalResiliency();
              }}
              disabled={selectedSubscriptionIds.length === 0}
              style={{
                width: 132,
                height: 34,
                padding: "6px 12px",
                background: selectedSubscriptionIds.length > 0 ? "#fff" : "#f3f2f1",
                color: selectedSubscriptionIds.length > 0 ? "#0078d4" : "#a0a09f",
                border: selectedSubscriptionIds.length > 0 ? "1px solid #8a8886" : "1px solid #d0d0d0",
                borderRadius: 4,
                cursor: selectedSubscriptionIds.length > 0 ? "pointer" : "not-allowed",
                fontSize: 12,
                fontWeight: 600,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 6,
                transition: "all 0.1s ease-in-out",
              }}
              title="Reload graph and zonal resilience data from server"
              onMouseEnter={e => {
                if (selectedSubscriptionIds.length > 0) {
                  e.currentTarget.style.background = "#f3f2f1";
                  e.currentTarget.style.borderColor = "#0078d4";
                }
              }}
              onMouseLeave={e => {
                if (selectedSubscriptionIds.length > 0) {
                  e.currentTarget.style.background = "#fff";
                  e.currentTarget.style.borderColor = "#8a8886";
                }
              }}
            >
              <ArrowSync16Regular />
              Reload
            </button>
            
            {/* Refresh annotations and scores button with badge */}
            {needsRefresh && (
              <div style={{ position: "relative" }}>
                <button
                  onClick={() => handleRefreshAnnotationsAndScores()}
                  disabled={isRefreshing || pendingRefreshCount === 0}
                  style={{
                    padding: "6px 12px",
                    background: isRefreshing || pendingRefreshCount === 0 ? "#444" : "#2ea043",
                    color: "#fff",
                    border: "1px solid #4a7c4e",
                    borderRadius: 4,
                    cursor: isRefreshing || pendingRefreshCount === 0 ? "not-allowed" : "pointer",
                    fontSize: 12,
                    fontWeight: 600,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                  title={
                    pendingRefreshCount > 0
                      ? `Re-run LLM annotations for ${pendingRefreshCount} subscription(s)`
                      : "No subscription changes to refresh"
                  }
                >
                  {isRefreshing ? "Refreshing..." : "✓ Refresh Annotations & Scores"}
                </button>
                <span
                  style={{
                    position: "absolute",
                    top: "-8px",
                    right: "-8px",
                    background: "#ff6b6b",
                    color: "#fff",
                    borderRadius: "50%",
                    width: 16,
                    height: 16,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 10,
                    fontWeight: 700,
                  }}
                    title={`${pendingRefreshCount} subscription(s) pending refresh`}
                >
                  {pendingRefreshCount}
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Tabbed View: Graph and Resiliency */}
        <div style={{ flex: 1, height: "100%", display: "flex", flexDirection: "column" }}>
          <TabbedView
            activeTab={activeTabIndex}
            onActiveTabChange={setActiveTabIndex}
            tabs={[
              {
                label: "Graph",
                icon: "📊",
                content: !hasSelection ? (
                  <div style={{ padding: "32px", textAlign: "center", color: "#6b7280" }}>
                    Select one or more subscriptions in the sidebar to load the graph.
                  </div>
                ) : (
                  <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column" }}>
                    <div ref={graphCaptureRef} style={{ flex: 1, position: "relative" }}>
                      <ReactFlowProvider>
                        <GraphCanvas
                          ref={graphCanvasRef}
                          nodes={nodesForView}
                          edges={edgesForView}
                          groups={graph?.groups ?? []}
                          graphViewState={pendingGraphView}
                          onGraphViewApplied={() => setPendingGraphView(null)}
                          onGraphViewChanged={() => setGraphViewRevision(prev => prev + 1)}
                          selectedEdgeId={selectedEdge?.id ?? null}
                          userLayerEnabled={userLayerEnabled}
                          aiLayerEnabled={aiLayerEnabled}
                          onAiLayerEnabledChange={setAiLayerEnabled}
                          onUserLayerEnabledChange={setUserLayerEnabled}
                          maxImportance={maxImportance}
                          onNodeSelected={handleNodeSelected}
                          onEdgeCreate={handleCreateManualLink}
                          onNodeRename={handleRenameNode}
                          onNodeHide={handleHideNode}
                          onNodeDragStart={() => { setSelectedNode(null); setSelectedEdge(null); }}
                          onGroupCreate={applyGroupToNodes}
                          groupCreateRequest={groupCreateRequest}
                          onRemoveNodeFromGroup={handleRemoveNodeFromGroup}
                          onSelectionStateChange={state => {
                            const prevSelection = lastGroupToolbarSelectionRef.current;
                            lastGroupToolbarSelectionRef.current = state;
                            setGroupToolbarSelection(state);

                            // Clear any stale Service Group status when the selection changes.
                            if (state.selectedGroupId !== prevSelection.selectedGroupId) {
                              setServiceGroupMessage(null);
                            }

                            // Initialize toolbar name when mode changes or selecting a different group.
                            if (state.selectedGroupId) {
                              lastSuggestedGroupNameRef.current = "";
                              setGroupToolbarName(prev => {
                                if (prev.trim().length === 0 || prev === (prevSelection.selectedGroupLabel ?? "")) {
                                  return state.selectedGroupLabel ?? "";
                                }
                                return prev;
                              });
                            } else if (state.selectedNodeIds.length > 1) {
                              const suggested = suggestGroupName(state.selectedNodeIds);
                              setGroupToolbarName(prev => {
                                const shouldReplace =
                                  prev.trim().length === 0 || prev === lastSuggestedGroupNameRef.current;
                                if (!shouldReplace) return prev;

                                lastSuggestedGroupNameRef.current = suggested;
                                return suggested;
                              });
                            } else {
                              lastSuggestedGroupNameRef.current = "";
                              setGroupToolbarName("");
                            }
                          }}
                          onMoveNodeToGroup={moveNodeToGroup}
                          onNodeRemoveFromGroup={handleNodeRemoveFromGroupClick}
                          onEdgeSelected={(e) => {
                            if (!e) {
                              setSelectedEdge(null);
                              setActiveSubscriptionId(null);
                              return;
                            }

                            setSelectedNode(null);
                            setSelectedEdge({
                              id: e.id,
                              source: e.source,
                              target: e.target,
                              relationship: e.relationship,
                              confidence: e.confidence,
                              status: e.status as any,
                              evidence: e.evidence,
                              origin: e.origin,
                              raw: e,
                            });
                            setActiveSubscriptionId(resolveSubscriptionIdForEdge(e.id));
                          }}
                        />
                      </ReactFlowProvider>
                    </div>
                  </div>
                ),
              },
              {
                label: "Overview",
                icon: "🛡️",
                content: !hasSelection ? (
                  <div style={{ padding: "32px", textAlign: "center", color: "#6b7280" }}>
                    Select one or more subscriptions to view resilience findings.
                  </div>
                ) : azNormalizedEvaluations ? (
                  <>
                    <div style={{ display: "flex", height: "100%", gap: "12px" }}>
                      <div style={{ flex: 1, overflow: "auto" }}>
                        <ResiliencySummary
                          evaluations={azNormalizedEvaluations || {}}
                          workloadScore={resilience_data?.workload_score}
                          subscriptionId={singleSubscriptionId ?? undefined}
                          subscriptionOptions={selectedSubscriptionOptions}
                          graphData={graph ?? undefined}
                          overrides={resilience_overrides}
                          viewLevel={viewLevel}
                          resourceGroupFilter={resourceGroupFilter}
                          serviceFilter={serviceFilter}
                          validationSourceFilter={validationSourceFilter}
                          highlightRecommendationId={selectedRecommendationFocus?.id}
                          highlightRecommendationTitle={selectedRecommendationFocus?.title}
                          onOverrideSaved={handleOverrideSaved}
                          onOverrideDeleted={handleOverrideDeleted}
                          onShowInGraph={handleShowInGraph}
                          onResourceSelect={handleOpenResourceDetails}
                        />
                      </div>
                    </div>
                  </>
                ) : (
                  <div style={{ padding: "32px", textAlign: "center", color: "#6b7280" }}>
                    No resilience data available
                  </div>
                ),
              },
              {
                label: "Zonal Resiliency",
                icon: "🌍",
                content: !hasSelection ? (
                  <div style={{ padding: "32px", textAlign: "center", color: "#6b7280" }}>
                    Select one or more subscriptions to view zonal resilience.
                  </div>
                ) : zonal_resilience_loading ? (
                  <div style={{ padding: "32px", textAlign: "center", color: "#6b7280" }}>
                    Loading zonal resilience data...
                  </div>
                ) : zonal_resilience_error ? (
                  <div style={{ padding: "32px", color: "#dc2626" }}>
                    <strong>Error:</strong> {zonal_resilience_error}
                  </div>
                ) : zonal_resilience_data ? (
                  <ZonalResiliencySummary 
                    data={zonal_resilience_data} 
                    graphData={graph ?? undefined}
                    resourceGroupFilter={resourceGroupFilter}
                    serviceFilter={serviceFilter}
                    regionAzCounts={regionAzCounts}
                    onResourceSelect={handleOpenResourceDetails}
                  />
                ) : (
                  <div style={{ padding: "32px", textAlign: "center", color: "#6b7280" }}>
                    No zonal resilience data available
                  </div>
                ),
              },
            ]}
            defaultTab={0}
          />
        </div>

        {isChatAvailable && hasSelection && (
          <ChatPanel
            subscriptionId={chatSubscriptionId}
            refreshToken={chatRefreshToken}
            getResourceLabel={getResourceLabel}
            mentionableResources={mentionableResources}
            onRecommendationSelect={handleRecommendationSelect}
            context={chatContext}
            onResourceHighlight={handleChatResourceHighlight}
            onEdgeSuggest={(edges) => {
              console.log("Chat suggested edges:", edges);
            }}
            height={800}
            isMinimized={true}
            mode="floating"
          />
        )}
      </div>

      {/* Right drawer */}
      {selectedEdge && (
        <EdgeDrawer
          edge={selectedEdge}
          onAccept={handleAcceptEdge}
          onReject={handleRejectEdge}
          onDelete={handleDeleteEdge}
          onReverseDirection={handleReverseEdgeDirection}
          onClose={() => setSelectedEdge(null)}
        />
      )}

      {selectedNode && (
        <NodeDrawer
          node={selectedNode}
          aiLayerEnabled={aiLayerEnabled}
          userLayerEnabled={userLayerEnabled}
          showShowInGraph={activeTabIndex !== 0}
          onShowInGraph={() => handleShowInGraph(selectedNode.id)}
          onClose={() => setSelectedNode(null)}
          onSave={handleSaveNode}
          onReset={() => handleResetNode(selectedNode.id)}
        />
      )}
      <LegendPanel open={showLegend} onClose={() => setShowLegend(false)} />

      {pendingServiceGroupCreate && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0, 0, 0, 0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
        >
          <div
            style={{
              width: 420,
              maxWidth: "90vw",
              background: "#fff",
              borderRadius: 6,
              boxShadow: "0 8px 24px rgba(0, 0, 0, 0.25)",
              padding: 20,
              color: "#323130",
              fontFamily: "Segoe UI, Tahoma, Geneva, Verdana, sans-serif",
            }}
          >
            <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>
              Create a Service Group?
            </div>
            <div style={{ fontSize: 13, lineHeight: 1.5, color: "#605e5c", marginBottom: 16 }}>
              This workload isn’t linked to an Azure Service Group yet. Create one from its{" "}
              {pendingServiceGroupCreate.memberIds.length} Azure resource
              {pendingServiceGroupCreate.memberIds.length === 1 ? "" : "s"}? Future saves will keep
              it in sync.
            </div>
            <div style={{ marginBottom: 16 }}>
              <label
                htmlFor="sg-parent-select"
                style={{ display: "block", fontSize: 12, fontWeight: 600, marginBottom: 4 }}
              >
                Parent
              </label>
              <select
                id="sg-parent-select"
                value={selectedParentServiceGroupId}
                onChange={(e) => setSelectedParentServiceGroupId(e.target.value)}
                disabled={serviceGroupBusy}
                style={{
                  width: "100%",
                  padding: "6px 8px",
                  fontSize: 13,
                  border: "1px solid #8a8886",
                  borderRadius: 2,
                  background: "#fff",
                  color: "#323130",
                }}
              >
                <option value="">Tenant root (top-level)</option>
                {parentServiceGroupOptions.map((sg) => (
                  <option key={sg.id} value={sg.id}>
                    {sg.display_name}
                  </option>
                ))}
              </select>
              <div style={{ fontSize: 11, color: "#605e5c", marginTop: 4 }}>
                Choose where the new Service Group sits in the hierarchy.
              </div>
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button
                type="button"
                onClick={() => setPendingServiceGroupCreate(null)}
                disabled={serviceGroupBusy}
                style={{
                  padding: "6px 14px",
                  background: "#fff",
                  color: "#323130",
                  border: "1px solid #8a8886",
                  borderRadius: 2,
                  fontSize: 13,
                  cursor: serviceGroupBusy ? "default" : "pointer",
                }}
              >
                Not now
              </button>
              <button
                type="button"
                onClick={confirmCreateServiceGroup}
                disabled={serviceGroupBusy}
                style={{
                  padding: "6px 14px",
                  background: "#0078d4",
                  color: "#fff",
                  border: "1px solid #0078d4",
                  borderRadius: 2,
                  fontSize: 13,
                  cursor: serviceGroupBusy ? "default" : "pointer",
                }}
              >
                {serviceGroupBusy ? "Creating…" : "Create Service Group"}
              </button>
            </div>
          </div>
        </div>
      )}

      {pendingWorkloadDelete && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0, 0, 0, 0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
        >
          <div
            style={{
              width: 520,
              maxWidth: "90vw",
              background: "#fff",
              borderRadius: 2,
              boxShadow: "0 8px 24px rgba(0, 0, 0, 0.25)",
              padding: 24,
              color: "#323130",
              fontFamily: "Segoe UI, Tahoma, Geneva, Verdana, sans-serif",
            }}
          >
            <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>
              Delete workload
            </div>
            <div style={{ fontSize: 13, lineHeight: 1.5, color: "#605e5c", marginBottom: 16 }}>
              “{pendingWorkloadDelete.workloadName}” is linked to the Azure Service Group{" "}
              “{pendingWorkloadDelete.serviceGroupDisplayName}”. Do you also want to delete that
              Service Group from Azure? This removes the Service Group and its member links, but
              never deletes the underlying Azure resources.
            </div>
            <div
              style={{
                display: "flex",
                justifyContent: "flex-end",
                flexWrap: "wrap",
                gap: 8,
              }}
            >
              <button
                type="button"
                onClick={() => setPendingWorkloadDelete(null)}
                disabled={serviceGroupDeleting}
                style={{
                  minHeight: 32,
                  padding: "5px 16px",
                  background: "#fff",
                  color: "#323130",
                  border: "1px solid #8a8886",
                  borderRadius: 0,
                  fontSize: 14,
                  fontWeight: 600,
                  cursor: serviceGroupDeleting ? "default" : "pointer",
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  const target = pendingWorkloadDelete;
                  setPendingWorkloadDelete(null);
                  void performWorkloadDelete(target.workloadId, null);
                }}
                disabled={serviceGroupDeleting}
                style={{
                  minHeight: 32,
                  padding: "5px 16px",
                  background: "#fff",
                  color: "#323130",
                  border: "1px solid #8a8886",
                  borderRadius: 0,
                  fontSize: 14,
                  fontWeight: 600,
                  cursor: serviceGroupDeleting ? "default" : "pointer",
                }}
              >
                Workload only
              </button>
              <button
                type="button"
                onClick={() => {
                  const target = pendingWorkloadDelete;
                  setPendingWorkloadDelete(null);
                  void performWorkloadDelete(target.workloadId, target.serviceGroupName);
                }}
                disabled={serviceGroupDeleting}
                style={{
                  minHeight: 32,
                  padding: "5px 16px",
                  background: "#a4262c",
                  color: "#fff",
                  border: "1px solid #a4262c",
                  borderRadius: 0,
                  fontSize: 14,
                  fontWeight: 600,
                  cursor: serviceGroupDeleting ? "default" : "pointer",
                }}
              >
                Delete both
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default WorkloadView;
