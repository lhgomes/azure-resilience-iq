import React from "react";
import type { ViewLevel } from "../domain/graphView";
import { AddRegular, DeleteRegular, EditRegular, Save16Regular } from "@fluentui/react-icons";
import { CloseIconButton, BulkSelectionButtons, IconButton, CopyToClipboardButton } from "./common/buttons";

export interface SubscriptionOption {
  id: string;
  name: string;
}

interface ResourceGroupOption {
  key: string;
  label: string;
}

interface ServiceOptionCategory {
  category: string;
  services: Array<{ key: string; label: string }>;
}

interface ValidationSourceOption {
  key: string;
  label: string;
}

interface Props {
  subscriptions: SubscriptionOption[];
  selectedSubscriptions: Set<string>;
  onSelectedSubscriptionsChange: (next: Set<string>) => void;

  workloads: Array<{ workload_id: string; name: string; updated_at?: string }>;
  activeWorkloadId: string | null;
  workloadName: string;
  onWorkloadNameChange: (next: string) => void;
  onWorkloadSelect: (workloadId: string | null) => void;
  onWorkloadCreate: () => void;
  onWorkloadSave: () => void;
  onWorkloadRename: () => void;
  onWorkloadDelete: () => void;
  workloadError?: string | null;
  workloadDirty?: boolean;
  workloadNewDirty?: boolean;

  viewLevel: ViewLevel;
  onViewLevelChange: (next: ViewLevel) => void;

  resourceGroupOptions: ResourceGroupOption[];
  resourceGroupFilter: Set<string>;
  onResourceGroupFilterChange: (next: Set<string>) => void;

  serviceOptions: ServiceOptionCategory[];
  serviceFilter: Set<string>;
  onServiceFilterChange: (next: Set<string>) => void;

  validationSourceOptions: ValidationSourceOption[];
  validationSourceFilter: Set<string>;
  onValidationSourceFilterChange: (next: Set<string>) => void;

  expandedCategories: Set<string>;
  onExpandedCategoriesChange: (next: Set<string>) => void;

  showLegend: boolean;
  onToggleLegend: () => void;

  availableSubscriptionsForMapping: Array<{
    id: string;
    name: string;
    state: string;
    mapped: boolean;
  }>;
  onRefreshAvailableSubscriptions: () => void;
  onDiscoverMappingResourceGroups: (subscriptionId: string) => Promise<string[]>;
  onStartSubscriptionMapping: (payload: {
    subscriptionId: string;
    resourceGroups: string[];
    tags: Record<string, string>;
  }) => void;
  onUploadTerraformScripts: (payload: {
    files: File[];
    subscriptionName: string;
  }) => Promise<{
    subscription_id: string;
    subscription_name: string;
    resource_count: number;
    edge_count: number;
    message: string;
  }>;
  mappingInProgress: boolean;
  mappingStatus?: {
    status?: string;
    progress?: number;
    current_stage?: string | null;
    message?: string;
    stages?: Array<{
      name: string;
      status: string;
      stdout_tail?: string;
      stderr_tail?: string;
      conversation_action?: "created" | "reused";
    }>;
  } | null;
  mappingError?: string | null;
  mappingAuthRequired?: boolean;
}

type MappingModalUiCache = {
  showMappingModal: boolean;
  activeMappingTab: "essentials" | "terraform" | "progress";
  mappingProgressLog: string[];
  showRawMappingLog: boolean;
  loggedProgressMilestones: string[];
  mappingSessionDismissed: boolean;
  mappingRunRequested: boolean;
};

const mappingModalUiCache: MappingModalUiCache = {
  showMappingModal: false,
  activeMappingTab: "essentials",
  mappingProgressLog: [],
  showRawMappingLog: false,
  loggedProgressMilestones: [],
  mappingSessionDismissed: false,
  mappingRunRequested: false,
};

const WorkloadSidebar: React.FC<Props> = props => {
  const showResourceGroupFilter = props.resourceGroupOptions.length >= 2;
  const hasSelection = props.selectedSubscriptions.size > 0;
  const [showMappingModal, setShowMappingModalState] = React.useState(mappingModalUiCache.showMappingModal);
  const [selectedMappingSubscriptionId, setSelectedMappingSubscriptionId] = React.useState("");
  const [mappingResourceGroups, setMappingResourceGroups] = React.useState<string[]>([]);
  const [selectedMappingResourceGroups, setSelectedMappingResourceGroups] = React.useState<Set<string>>(new Set());
  const [mappingResourceGroupsLoading, setMappingResourceGroupsLoading] = React.useState(false);
  const [mappingResourceGroupsError, setMappingResourceGroupsError] = React.useState<string | null>(null);
  const [tagsInput, setTagsInput] = React.useState("");
  const [activeMappingTab, setActiveMappingTabState] = React.useState<"essentials" | "terraform" | "progress">(mappingModalUiCache.activeMappingTab);
  const [terraformSubscriptionName, setTerraformSubscriptionName] = React.useState("Terraform");
  const [terraformFiles, setTerraformFiles] = React.useState<File[]>([]);
  const [terraformUploadLoading, setTerraformUploadLoading] = React.useState(false);
  const [terraformUploadError, setTerraformUploadError] = React.useState<string | null>(null);
  const [terraformUploadSuccess, setTerraformUploadSuccess] = React.useState<string | null>(null);
  const terraformFileInputRef = React.useRef<HTMLInputElement | null>(null);
  const [mappingProgressLog, setMappingProgressLogState] = React.useState<string[]>(mappingModalUiCache.mappingProgressLog);
  const [showRawMappingLog, setShowRawMappingLogState] = React.useState(mappingModalUiCache.showRawMappingLog);
  const mappingProgressTextareaRef = React.useRef<HTMLTextAreaElement | null>(null);
  const loggedProgressMilestonesRef = React.useRef<Set<string>>(new Set(mappingModalUiCache.loggedProgressMilestones));
  const [mappingSessionDismissed, setMappingSessionDismissedState] = React.useState(mappingModalUiCache.mappingSessionDismissed);
  const [mappingRunRequested, setMappingRunRequestedState] = React.useState(mappingModalUiCache.mappingRunRequested);

  const setShowMappingModal = React.useCallback((next: boolean | ((prev: boolean) => boolean)) => {
    setShowMappingModalState(prev => {
      const resolved = typeof next === "function" ? next(prev) : next;
      mappingModalUiCache.showMappingModal = resolved;
      return resolved;
    });
  }, []);

  const setActiveMappingTab = React.useCallback((next: "essentials" | "terraform" | "progress" | ((prev: "essentials" | "terraform" | "progress") => "essentials" | "terraform" | "progress")) => {
    setActiveMappingTabState(prev => {
      const resolved = typeof next === "function" ? next(prev) : next;
      mappingModalUiCache.activeMappingTab = resolved;
      return resolved;
    });
  }, []);

  const setMappingProgressLog = React.useCallback((
    next: string[] | ((prev: string[]) => string[])
  ) => {
    setMappingProgressLogState(prev => {
      const resolved = typeof next === "function" ? next(prev) : next;
      mappingModalUiCache.mappingProgressLog = resolved;
      return resolved;
    });
  }, []);

  const setShowRawMappingLog = React.useCallback((next: boolean | ((prev: boolean) => boolean)) => {
    setShowRawMappingLogState(prev => {
      const resolved = typeof next === "function" ? next(prev) : next;
      mappingModalUiCache.showRawMappingLog = resolved;
      return resolved;
    });
  }, []);

  const setMappingSessionDismissed = React.useCallback((next: boolean | ((prev: boolean) => boolean)) => {
    setMappingSessionDismissedState(prev => {
      const resolved = typeof next === "function" ? next(prev) : next;
      mappingModalUiCache.mappingSessionDismissed = resolved;
      return resolved;
    });
  }, []);

  const setMappingRunRequested = React.useCallback((next: boolean | ((prev: boolean) => boolean)) => {
    setMappingRunRequestedState(prev => {
      const resolved = typeof next === "function" ? next(prev) : next;
      mappingModalUiCache.mappingRunRequested = resolved;
      return resolved;
    });
  }, []);

  React.useEffect(() => {
    const available = props.availableSubscriptionsForMapping;
    if (available.length === 0) {
      setSelectedMappingSubscriptionId("");
      return;
    }

    const stillValid = available.some(sub => sub.id === selectedMappingSubscriptionId);
    if (!stillValid) {
      setSelectedMappingSubscriptionId(available[0].id);
    }
  }, [props.availableSubscriptionsForMapping, selectedMappingSubscriptionId]);

  const parseTags = React.useCallback((input: string): Record<string, string> => {
    const tags: Record<string, string> = {};
    input
      .split(",")
      .map(part => part.trim())
      .filter(Boolean)
      .forEach(part => {
        const separatorIndex = part.indexOf("=");
        if (separatorIndex <= 0) return;
        const key = part.slice(0, separatorIndex).trim();
        const value = part.slice(separatorIndex + 1).trim();
        if (!key || !value) return;
        tags[key] = value;
      });
    return tags;
  }, []);

  const formatLogTimestamp = React.useCallback((date: Date = new Date()) => {
    return date.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  }, []);

  const appendProgressLog = React.useCallback((message: string, key?: string) => {
    const trimmed = message.trim();
    if (!trimmed) return;

    if (key) {
      if (loggedProgressMilestonesRef.current.has(key)) return;
      loggedProgressMilestonesRef.current.add(key);
      mappingModalUiCache.loggedProgressMilestones = Array.from(loggedProgressMilestonesRef.current);
    }

    setMappingProgressLog(prev => {
      const timestamped = `[${formatLogTimestamp()}] ${trimmed}`;
      if (prev[prev.length - 1] === timestamped) return prev;
      return [...prev, timestamped];
    });
  }, [formatLogTimestamp]);

  const stageTitle = React.useCallback((stageName?: string | null) => {
    if (!stageName) return "";
    if (stageName === "collector") return "Collecting resources";
    if (stageName === "conversation") return "Creating conversation context";
    if (stageName === "resilience") return "Running resilience analysis";
    if (stageName === "llm") return "Generating LLM insights";
    return stageName;
  }, []);

  const extractCollectedResourcesCount = React.useCallback((stdoutTail?: string) => {
    if (!stdoutTail) return null;
    const patterns = [
      /(\d+)\s+resources?/i,
      /resources?\s*[:=]\s*(\d+)/i,
      /collected\s+(\d+)/i,
      /found\s+(\d+)/i,
    ];

    for (const pattern of patterns) {
      const match = stdoutTail.match(pattern);
      if (match?.[1]) return Number(match[1]);
    }
    return null;
  }, []);

  const extractEvaluatedRecommendationsCount = React.useCallback((stdoutTail?: string, stderrTail?: string) => {
    const combined = `${stdoutTail ?? ""}\n${stderrTail ?? ""}`;
    if (!combined.trim()) return null;

    const patterns = [
      /(\d+)\s+recommendations?\s+evaluated/i,
      /evaluated\s+(\d+)\s+recommendations?/i,
      /checks?\s*evaluated\s*[:=]\s*(\d+)/i,
      /evaluations?\s*[:=]\s*(\d+)/i,
    ];

    for (const pattern of patterns) {
      const match = combined.match(pattern);
      if (match?.[1]) return Number(match[1]);
    }
    return null;
  }, []);

  React.useEffect(() => {
    if (mappingRunRequested) return;
    if (!showMappingModal) return;

    if (props.availableSubscriptionsForMapping.length > 0) {
      appendProgressLog(
        `${props.availableSubscriptionsForMapping.length} subscriptions discovered`,
        `subscriptions-discovered-${props.availableSubscriptionsForMapping.length}`
      );
    } else {
      appendProgressLog("No subscriptions discovered", "subscriptions-discovered-none");
    }
  }, [appendProgressLog, mappingRunRequested, props.availableSubscriptionsForMapping, showMappingModal]);

  React.useEffect(() => {
    if (mappingRunRequested) return;
    if (!showMappingModal || !selectedMappingSubscriptionId) return;
    appendProgressLog(`Selected subscription ${selectedMappingSubscriptionId}`, `selected-subscription-${selectedMappingSubscriptionId}`);
  }, [appendProgressLog, mappingRunRequested, selectedMappingSubscriptionId, showMappingModal]);

  React.useEffect(() => {
    if (mappingRunRequested) return;
    if (!showMappingModal || !selectedMappingSubscriptionId) {
      setMappingResourceGroups([]);
      setSelectedMappingResourceGroups(new Set());
      setMappingResourceGroupsError(null);
      return;
    }

    let isActive = true;
    setMappingResourceGroupsLoading(true);
    setMappingResourceGroupsError(null);
    appendProgressLog("Querying resource groups", `resource-groups-query-${selectedMappingSubscriptionId}`);

    props.onDiscoverMappingResourceGroups(selectedMappingSubscriptionId)
      .then(groups => {
        if (!isActive) return;
        setMappingResourceGroups(groups);
        setSelectedMappingResourceGroups(new Set(groups));
        appendProgressLog(
          `${groups.length} resource groups discovered`,
          `resource-groups-discovered-${selectedMappingSubscriptionId}-${groups.length}`
        );
      })
      .catch((err: any) => {
        if (!isActive) return;
        setMappingResourceGroups([]);
        setSelectedMappingResourceGroups(new Set());
        setMappingResourceGroupsError(err?.message ?? "Failed to discover resource groups");
        appendProgressLog(
          `Resource group query failed: ${err?.message ?? "Failed to discover resource groups"}`,
          `resource-groups-failed-${selectedMappingSubscriptionId}`
        );
      })
      .finally(() => {
        if (!isActive) return;
        setMappingResourceGroupsLoading(false);
      });

    return () => {
      isActive = false;
    };
  }, [appendProgressLog, mappingRunRequested, showMappingModal, selectedMappingSubscriptionId, props.onDiscoverMappingResourceGroups]);

  const closeMappingModal = React.useCallback(() => {
    if (props.mappingInProgress || terraformUploadLoading) return;

    setShowMappingModal(false);
    setActiveMappingTab("essentials");
    setTagsInput("");
    setMappingResourceGroups([]);
    setSelectedMappingResourceGroups(new Set());
    setMappingResourceGroupsError(null);
    setTerraformFiles([]);
    setTerraformSubscriptionName("Terraform");
    setTerraformUploadError(null);
    setTerraformUploadSuccess(null);
    setMappingSessionDismissed(true);
  }, [props.mappingInProgress, terraformUploadLoading]);

  const handleStartMapping = React.useCallback(() => {
    if (!selectedMappingSubscriptionId || props.mappingInProgress) return;

    const selectedSubscription = props.availableSubscriptionsForMapping.find(
      sub => sub.id === selectedMappingSubscriptionId
    );
    const selectedResourceGroups = Array.from(selectedMappingResourceGroups);
    const parsedTags = parseTags(tagsInput);
    const tagsSummary = Object.keys(parsedTags).length === 0
      ? "No tags"
      : Object.entries(parsedTags)
        .map(([key, value]) => `${key}=${value}`)
        .join(", ");
    const rgSummary = selectedResourceGroups.length === 0
      ? "All resource groups"
      : `${selectedResourceGroups.length} selected: ${selectedResourceGroups.join(", ")}`;

    setActiveMappingTab("progress");
    setShowMappingModal(true);
    setMappingSessionDismissed(false);
    setMappingRunRequested(true);
    appendProgressLog("Mapping request received", "mapping-request-received");
    appendProgressLog(
      `Subscription: ${selectedSubscription?.name ?? selectedMappingSubscriptionId} (${selectedMappingSubscriptionId})`,
      "mapping-subscription"
    );
    appendProgressLog(`Resource groups: ${rgSummary}`, "mapping-resource-groups");
    appendProgressLog(`Tags: ${tagsSummary}`, "mapping-tags");
    appendProgressLog("Pipeline: Collecting → LLM Insights → Resilience Analysis", "mapping-pipeline");
    appendProgressLog(`Starting mapping for ${selectedMappingSubscriptionId}`, "mapping-started");
    appendProgressLog("Collecting resources", "collector-running");

    props.onStartSubscriptionMapping({
      subscriptionId: selectedMappingSubscriptionId,
      resourceGroups: selectedResourceGroups,
      tags: parsedTags,
    });
  }, [
    parseTags,
    props,
    appendProgressLog,
    selectedMappingResourceGroups,
    selectedMappingSubscriptionId,
    tagsInput,
  ]);

  const handleTerraformFileSelection = React.useCallback((files: File[]) => {
    const acceptedFiles = files.filter(file => file.name.endsWith(".tf") || file.name.endsWith(".json"));
    setTerraformFiles(acceptedFiles);
    setTerraformUploadError(acceptedFiles.length > 0 ? null : "Please select Terraform .tf or .json files");
    setTerraformUploadSuccess(null);
  }, []);

  const handleTerraformUpload = React.useCallback(async () => {
    if (terraformFiles.length === 0 || terraformUploadLoading) {
      if (terraformFiles.length === 0) {
        setTerraformUploadError("Please select Terraform .tf or .json files");
      }
      return;
    }

    setTerraformUploadLoading(true);
    setTerraformUploadError(null);
    setTerraformUploadSuccess(null);
    setActiveMappingTab("progress");
    setShowMappingModal(true);
    setMappingSessionDismissed(false);
    setMappingRunRequested(true);

    const fileLabel = terraformFiles.length === 1 ? terraformFiles[0].name : `${terraformFiles.length} files`;
    appendProgressLog("Terraform upload request received", "terraform-request-received");
    appendProgressLog(`Source: ${fileLabel}`, "terraform-files");
    appendProgressLog("Pipeline: Terraform Collector → Conversation → LLM Insights → Resilience Analysis", "terraform-pipeline");
    appendProgressLog("Running Terraform collector", "collector-running");

    try {
      const response = await props.onUploadTerraformScripts({
        files: terraformFiles,
        subscriptionName: terraformSubscriptionName.trim() || "Terraform",
      });
      setTerraformUploadSuccess(
        `Upload complete: ${response.resource_count} resources and ${response.edge_count} edges generated.`
      );
      appendProgressLog(
        `Terraform collector produced ${response.resource_count} resources and ${response.edge_count} edges`,
        "collector-completed"
      );
      setTerraformFiles([]);
      if (terraformFileInputRef.current) {
        terraformFileInputRef.current.value = "";
      }
    } catch (err: any) {
      setTerraformUploadError(err?.message ?? "Terraform upload failed");
    } finally {
      setTerraformUploadLoading(false);
    }
  }, [props, terraformFiles, terraformSubscriptionName, terraformUploadLoading]);

  React.useEffect(() => {
    const hasMappingUpdates = props.mappingInProgress || !!props.mappingStatus || !!props.mappingError;
    if (mappingSessionDismissed || !hasMappingUpdates) return;
    if (!showMappingModal) {
      setShowMappingModal(true);
    }
  }, [
    mappingSessionDismissed,
    props.mappingError,
    props.mappingInProgress,
    props.mappingStatus,
    showMappingModal,
  ]);

  React.useEffect(() => {
    const shouldConsumeMappingStatus = props.mappingInProgress || mappingRunRequested;
    if (!shouldConsumeMappingStatus) return;
    if (!showMappingModal || !props.mappingStatus) return;

    const currentStage = props.mappingStatus.current_stage;
    if (currentStage) {
      appendProgressLog(stageTitle(currentStage), `${currentStage}-running`);
    }

    const stages = props.mappingStatus.stages ?? [];
    stages.forEach(stage => {
      if (stage.status !== "completed") return;

      if (stage.name === "collector") {
        const collectorOutput = (stage as { stdout_tail?: string }).stdout_tail;
        const count = extractCollectedResourcesCount(collectorOutput);
        if (typeof count === "number" && Number.isFinite(count)) {
          appendProgressLog(`Collected ${count} resources`, "collector-completed");
        } else {
          appendProgressLog("Collected resources", "collector-completed");
        }
      } else if (stage.name === "conversation") {
        if (stage.conversation_action === "created") {
          appendProgressLog("Conversation context created", "conversation-completed-created");
        } else {
          appendProgressLog("Conversation context reused", "conversation-completed-reused");
        }
      } else if (stage.name === "resilience") {
        appendProgressLog("Resilience analysis completed", "resilience-completed");
        const recommendationsCount = extractEvaluatedRecommendationsCount(
          (stage as { stdout_tail?: string }).stdout_tail,
          (stage as { stderr_tail?: string }).stderr_tail
        );
        if (typeof recommendationsCount === "number" && Number.isFinite(recommendationsCount)) {
          appendProgressLog(`Evaluated ${recommendationsCount} recommendations`, "resilience-recommendations-evaluated");
        }
      } else if (stage.name === "llm") {
        appendProgressLog("LLM insights generated", "llm-completed");
      }
    });

    if (props.mappingStatus.status === "completed") {
      appendProgressLog("Process completed", "mapping-completed");
    }

    if (props.mappingStatus.status === "failed" && props.mappingStatus.message) {
      appendProgressLog(`Mapping failed: ${props.mappingStatus.message}`, "mapping-failed");
    }
  }, [
    appendProgressLog,
    extractEvaluatedRecommendationsCount,
    extractCollectedResourcesCount,
    mappingRunRequested,
    props.mappingInProgress,
    props.mappingStatus,
    showMappingModal,
    stageTitle,
  ]);

  React.useEffect(() => {
    const shouldConsumeMappingStatus = props.mappingInProgress || mappingRunRequested;
    if (!shouldConsumeMappingStatus) return;
    if (!showMappingModal || !props.mappingError) return;
    appendProgressLog(`Mapping failed: ${props.mappingError}`, "mapping-error");
  }, [appendProgressLog, mappingRunRequested, props.mappingError, props.mappingInProgress, showMappingModal]);

  // Shared button base styles
  const buttonBase: React.CSSProperties = {
    padding: "4px 12px",
    fontSize: 13,
    fontWeight: 400,
    cursor: "pointer",
    border: "1px solid",
    transition: "all 0.1s ease-in-out",
    outline: "none",
    lineHeight: "20px",
    borderRadius: 6
  };

  const iconButton: React.CSSProperties = {
    padding: "6px 8px",
    fontSize: 16,
    fontWeight: 400,
    borderRadius: 2,
    cursor: "pointer",
    border: "1px solid transparent",
    background: "transparent",
    transition: "all 0.1s ease-in-out",
    outline: "none",
    minWidth: 32,
    height: 32,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  };

  const primaryButton: React.CSSProperties = {
    ...buttonBase,
    background: "#0078d4",
    color: "#fff",
    borderColor: "#0078d4",
  };

  const secondaryButton: React.CSSProperties = {
    ...buttonBase,
    background: "transparent",
    color: "#0078d4",
    borderColor: "#8a8886",
  };

  const dangerButton: React.CSSProperties = {
    ...buttonBase,
    background: "transparent",
    color: "#a4262c",
    borderColor: "#8a8886",
  };

  const disabledButton: React.CSSProperties = {
    ...buttonBase,
    background: "#f3f2f1",
    color: "#a19f9d",
    borderColor: "#c8c6c4",
    cursor: "not-allowed",
  };

  const mappingProgressText = mappingProgressLog.length > 0
    ? mappingProgressLog.join("\n")
    : "Waiting for mapping to start...";

  const mappingRawLogText = React.useMemo(() => {
    const status = props.mappingStatus;
    if (!status) return "No raw output yet.";

    const lines: string[] = [];
    if (status.status) lines.push(`status=${status.status}`);
    if (typeof status.progress === "number") lines.push(`progress=${status.progress}`);
    if (status.current_stage) lines.push(`current_stage=${status.current_stage}`);
    if (status.message) lines.push(`message=${status.message}`);

    const stages = status.stages ?? [];
    stages.forEach(stage => {
      lines.push("");
      lines.push(`[stage:${stage.name}] status=${stage.status}`);
      if (stage.conversation_action) {
        lines.push(`conversation_action=${stage.conversation_action}`);
      }
      if (stage.stdout_tail && stage.stdout_tail.trim()) {
        lines.push("stdout_tail:");
        lines.push(stage.stdout_tail);
      }
      if (stage.stderr_tail && stage.stderr_tail.trim()) {
        lines.push("stderr_tail:");
        lines.push(stage.stderr_tail);
      }
    });

    return lines.length > 0 ? lines.join("\n") : "No raw output yet.";
  }, [props.mappingStatus]);

  const mappingDisplayedLogText = showRawMappingLog ? mappingRawLogText : mappingProgressText;

  const mappingStatusMessage = React.useMemo(
    () => (props.mappingStatus?.message ?? "").trim(),
    [props.mappingStatus?.message]
  );

  const mappingProgressPercent = React.useMemo(() => {
    if (typeof props.mappingStatus?.progress !== "number") return null;
    return Math.max(0, Math.min(100, Math.round(props.mappingStatus.progress)));
  }, [props.mappingStatus?.progress]);

  const shouldShowMappingStatus = props.mappingInProgress || mappingRunRequested;

  React.useEffect(() => {
    if (!showMappingModal || activeMappingTab !== "progress") return;
    const textArea = mappingProgressTextareaRef.current;
    if (!textArea) return;
    textArea.scrollTop = textArea.scrollHeight;
  }, [activeMappingTab, mappingDisplayedLogText, showMappingModal]);

  return (
    <div
      style={{
        padding: "16px 12px",
        overflowY: "auto",
        overflowX: "hidden",
        color: "#323130",
      }}
    >
      {/* Workload Section */}
      <div style={{ marginBottom: 20 }}>
        <h3 style={{ 
          margin: "0 0 8px 0", 
          fontSize: 13, 
          fontWeight: 600,
          color: "#323130",
          textTransform: "uppercase",
          letterSpacing: "0.5px"
        }}>
          Workload
        </h3>
        
        <select
          value={props.activeWorkloadId ?? ""}
          onChange={e => props.onWorkloadSelect(e.target.value || null)}
          style={{
            width: "100%",
            background: "#fff",
            color: "#323130",
            border: "1px solid #8a8886",
            padding: "5px 8px",
            borderRadius: 2,
            fontSize: 14,
            marginBottom: 8,
            cursor: "pointer",
            outline: "none",
          }}
          onFocus={e => e.target.style.borderColor = "#0078d4"}
          onBlur={e => e.target.style.borderColor = "#8a8886"}
        >
          <option value="">Select workload</option>
          {props.workloads.map(workload => (
            <option key={workload.workload_id} value={workload.workload_id}>
              {workload.name}
            </option>
          ))}
        </select>

        <input
          type="text"
          value={props.workloadName}
          onChange={e => props.onWorkloadNameChange(e.target.value)}
          placeholder="Workload name"
          style={{
            width: "100%",
            background: "#fff",
            color: "#323130",
            border: "1px solid #8a8886",
            padding: "5px 8px",
            borderRadius: 2,
            fontSize: 14,
            marginBottom: 8,
            boxSizing: "border-box",
            outline: "none",
          }}
          onFocus={e => e.target.style.borderColor = "#0078d4"}
          onBlur={e => e.target.style.borderColor = "#8a8886"}
        />

        <div style={{ display: "flex", gap: 4 }}>
          <button
            onClick={props.onWorkloadCreate}
            disabled={!props.workloadName.trim()}
            title="Save as new workload"
            style={{
              ...iconButton,
              color: props.workloadName.trim() ? (!props.activeWorkloadId && props.workloadNewDirty ? "#fff" : "#0078d4") : "#c8c6c4",
              background: props.workloadName.trim() && !props.activeWorkloadId && props.workloadNewDirty ? "#0078d4" : "transparent",
              cursor: props.workloadName.trim() ? "pointer" : "not-allowed",
            }}
            onMouseEnter={e => {
              if (props.workloadName.trim()) {
                e.currentTarget.style.background = props.workloadNewDirty && !props.activeWorkloadId ? "#106ebe" : "#f3f2f1";
              }
            }}
            onMouseLeave={e => {
              if (props.workloadName.trim()) {
                e.currentTarget.style.background = props.workloadNewDirty && !props.activeWorkloadId ? "#0078d4" : "transparent";
              }
            }}
          >
            <AddRegular style={{ fontSize: 16 }} />
          </button>
          <button
            onClick={props.onWorkloadSave}
            disabled={!props.activeWorkloadId}
            title="Save workload"
            style={{
              ...iconButton,
              color: props.activeWorkloadId ? (props.workloadDirty ? "#fff" : "#0078d4") : "#c8c6c4",
              background: props.activeWorkloadId && props.workloadDirty ? "#107c10" : "transparent",
              cursor: props.activeWorkloadId ? "pointer" : "not-allowed",
            }}
            onMouseEnter={e => {
              if (props.activeWorkloadId) {
                e.currentTarget.style.background = props.workloadDirty ? "#0e6b0e" : "#f3f2f1";
              }
            }}
            onMouseLeave={e => {
              if (props.activeWorkloadId) {
                e.currentTarget.style.background = props.workloadDirty ? "#107c10" : "transparent";
              }
            }}
          >
            <Save16Regular style={{ fontSize: 16 }} />
          </button>
          <button
            onClick={props.onWorkloadRename}
            disabled={!props.activeWorkloadId || !props.workloadName.trim()}
            title="Rename workload"
            style={{
              ...iconButton,
              color: (props.activeWorkloadId && props.workloadName.trim()) ? "#605e5c" : "#c8c6c4",
              cursor: (props.activeWorkloadId && props.workloadName.trim()) ? "pointer" : "not-allowed",
            }}
            onMouseEnter={e => {
              if (props.activeWorkloadId && props.workloadName.trim()) {
                e.currentTarget.style.background = "#f3f2f1";
              }
            }}
            onMouseLeave={e => {
              if (props.activeWorkloadId && props.workloadName.trim()) {
                e.currentTarget.style.background = "transparent";
              }
            }}
          >
            <EditRegular style={{ fontSize: 16 }} />
          </button>
          <button
            onClick={props.onWorkloadDelete}
            disabled={!props.activeWorkloadId}
            title="Delete workload"
            style={{
              ...iconButton,
              color: props.activeWorkloadId ? "#a4262c" : "#c8c6c4",
              cursor: props.activeWorkloadId ? "pointer" : "not-allowed",
            }}
            onMouseEnter={e => {
              if (props.activeWorkloadId) {
                e.currentTarget.style.background = "#fde7e9";
              }
            }}
            onMouseLeave={e => {
              if (props.activeWorkloadId) {
                e.currentTarget.style.background = "transparent";
              }
            }}
          >
            <DeleteRegular style={{ fontSize: 16 }} />
          </button>
        </div>

        {props.workloadError && (
          <div style={{ fontSize: 12, color: "#d13438", marginTop: 6 }}>{props.workloadError}</div>
        )}
      </div>

      {/* Subscription Selector */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: "#323130", textTransform: "uppercase", letterSpacing: "0.5px" }}>Subscriptions</h3>
          <div style={{ display: "flex", gap: 4 }}>
            <IconButton
              onClick={() => {
                setShowMappingModal(true);
                setMappingSessionDismissed(false);
                setMappingRunRequested(false);
                setActiveMappingTab("essentials");
                setTagsInput("");
                setMappingResourceGroups([]);
                setSelectedMappingResourceGroups(new Set());
                setMappingResourceGroupsError(null);
                setTerraformFiles([]);
                setTerraformSubscriptionName("Terraform");
                setTerraformUploadError(null);
                setTerraformUploadSuccess(null);
                setShowRawMappingLog(false);
                setMappingProgressLog([]);
                loggedProgressMilestonesRef.current.clear();
                mappingModalUiCache.loggedProgressMilestones = [];
                appendProgressLog("Querying subscriptions", "subscriptions-query");
                props.onRefreshAvailableSubscriptions();
              }}
              title="Map New Workload"
              ariaLabel="Map new workload"
              variant="success"
            >
              <AddRegular style={{ fontSize: 16 }} />
            </IconButton>
            <BulkSelectionButtons
              onSelectAll={() => {
                const allSubscriptions = props.subscriptions.map(s => s.id);
                props.onSelectedSubscriptionsChange(new Set(allSubscriptions));
              }}
              onUncheckAll={() => {
                props.onSelectedSubscriptionsChange(new Set());
              }}
            />
          </div>
        </div>
        <div
          style={{
            maxHeight: 180,
            overflowY: "auto",
            border: "1px solid #8a8886",
            borderRadius: 2,
            padding: "4px 8px",
            background: "#fff",
          }}
        >
          {props.subscriptions.length === 0 ? (
            <div style={{ fontSize: 13, color: "#605e5c", padding: "8px 0" }}>
              No subscriptions available
            </div>
          ) : (
            props.subscriptions.map(sub => (
              <label
                key={sub.id}
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 8,
                  padding: "6px 4px",
                  cursor: "pointer",
                  fontSize: 14,
                  color: "#323130",
                  borderRadius: 2,
                }}
                onMouseEnter={e => e.currentTarget.style.background = "#f3f2f1"}
                onMouseLeave={e => e.currentTarget.style.background = "transparent"}
              >
                <input
                  type="checkbox"
                  checked={props.selectedSubscriptions.has(sub.id)}
                  onChange={e => {
                    const next = new Set(props.selectedSubscriptions);
                    if (e.target.checked) next.add(sub.id);
                    else next.delete(sub.id);
                    props.onSelectedSubscriptionsChange(next);
                  }}
                  style={{ marginTop: 3, flexShrink: 0, cursor: "pointer" }}
                />
                <div style={{ flex: 1, wordBreak: "break-word" }}>
                  <div style={{ fontWeight: 400 }}>{sub.name}</div>
                  <div style={{ fontSize: 12, color: "#605e5c", marginTop: 2 }}>{sub.id}</div>
                </div>
              </label>
            ))
          )}
        </div>

      </div>

      {hasSelection && (
        <>
          {/* View Level */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <h3 style={{ 
                margin: 0, 
                fontSize: 13, 
                fontWeight: 600, 
                color: "#323130",
                textTransform: "uppercase",
                letterSpacing: "0.5px"
              }}>
                View Level
              </h3>
              <span style={{ fontSize: 12, color: "#605e5c", fontWeight: 600 }}>
                {props.viewLevel === "overview" ? "L1" : props.viewLevel === "network" ? "L2" : "L3"}
              </span>
            </div>
            
            <input
              type="range"
              min="0"
              max="2"
              value={props.viewLevel === "overview" ? 0 : props.viewLevel === "network" ? 1 : 2}
              onChange={e => {
                const levels: Array<"overview" | "network" | "full"> = ["overview", "network", "full"];
                props.onViewLevelChange(levels[parseInt(e.target.value)]);
              }}
              style={{
                width: "100%",
                cursor: "pointer",
                height: 4,
                outline: "none",
                background: "#e0e0e0",
                borderRadius: 2,
              }}
            />
            
            <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontSize: 12, color: "#605e5c" }}>
              <span>Overview</span>
              <span></span>
              <span>Full</span>
            </div>
          </div>

          {showResourceGroupFilter && (
            <div style={{ marginBottom: 20 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: "#323130", textTransform: "uppercase", letterSpacing: "0.5px" }}>Resource Groups</h3>
                <BulkSelectionButtons
                  onSelectAll={() => {
                    const allGroups = props.resourceGroupOptions.map(rg => rg.key);
                    props.onResourceGroupFilterChange(new Set(allGroups));
                  }}
                  onUncheckAll={() => {
                    props.onResourceGroupFilterChange(new Set());
                  }}
                />
              </div>
              <div
                style={{
                  maxHeight: 200,
                  overflowY: "auto",
                  border: "1px solid #8a8886",
                  borderRadius: 2,
                  padding: "4px 8px",
                  background: "#fff",
                }}
              >
                {props.resourceGroupOptions.map(rg => (
                  <label
                    key={rg.key}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      padding: "6px 4px",
                      cursor: "pointer",
                      fontSize: 14,
                      color: "#323130",
                      borderRadius: 2,
                    }}
                    onMouseEnter={e => e.currentTarget.style.background = "#f3f2f1"}
                    onMouseLeave={e => e.currentTarget.style.background = "transparent"}
                  >
                    <input
                      type="checkbox"
                      checked={props.resourceGroupFilter.has(rg.key)}
                      onChange={e => {
                        const next = new Set(props.resourceGroupFilter);
                        if (e.target.checked) next.add(rg.key);
                        else next.delete(rg.key);
                        props.onResourceGroupFilterChange(next);
                      }}
                      style={{ cursor: "pointer" }}
                    />
                    {rg.label}
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Services */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: "#323130", textTransform: "uppercase", letterSpacing: "0.5px" }}>Services</h3>
              <BulkSelectionButtons
                onSelectAll={() => {
                  const allServices = props.serviceOptions.flatMap(cat => cat.services.map(s => s.key));
                  props.onServiceFilterChange(new Set(allServices));
                }}
                onUncheckAll={() => {
                  props.onServiceFilterChange(new Set());
                }}
              />
            </div>
            <div
              style={{
                maxHeight: 400,
                overflowY: "auto",
                border: "1px solid #8a8886",
                borderRadius: 2,
                padding: "4px 8px",
                background: "#fff",
              }}
            >
              {props.serviceOptions.map(category => {
                const allServicesInCategory = category.services.map(s => s.key);
                const selectedServicesInCategory = allServicesInCategory.filter(key => props.serviceFilter.has(key));
                const isExpanded = props.expandedCategories.has(category.category);
                const allSelected = selectedServicesInCategory.length === allServicesInCategory.length;
                const someSelected = selectedServicesInCategory.length > 0 && !allSelected;

                return (
                  <div key={category.category} style={{ marginBottom: 2 }}>
                    <div 
                      style={{ 
                        display: "flex", 
                        alignItems: "center", 
                        gap: 8, 
                        padding: "6px 8px",
                        borderRadius: 2,
                        cursor: "pointer",
                      }}
                      onClick={() => {
                        const next = new Set(props.expandedCategories);
                        if (isExpanded) next.delete(category.category);
                        else next.add(category.category);
                        props.onExpandedCategoriesChange(next);
                      }}
                      onMouseEnter={e => e.currentTarget.style.background = "#f3f2f1"}
                      onMouseLeave={e => e.currentTarget.style.background = "transparent"}
                    >
                      <span
                        style={{
                          color: "#605e5c",
                          fontSize: 10,
                          width: 12,
                          display: "inline-block",
                          textAlign: "center",
                        }}
                      >
                        {isExpanded ? "▼" : "▶"}
                      </span>

                      <input
                        type="checkbox"
                        checked={allSelected}
                        ref={el => {
                          if (el) el.indeterminate = someSelected;
                        }}
                        onChange={e => {
                          e.stopPropagation();
                          const newFilter = new Set(props.serviceFilter);
                          if (e.target.checked) allServicesInCategory.forEach(key => newFilter.add(key));
                          else allServicesInCategory.forEach(key => newFilter.delete(key));
                          props.onServiceFilterChange(newFilter);
                        }}
                        onClick={e => e.stopPropagation()}
                        style={{ cursor: "pointer", margin: 0 }}
                      />

                      <span style={{ fontSize: 14, fontWeight: 600, color: "#323130", flex: 1 }}>
                        {category.category}
                      </span>
                      
                      <span style={{ fontSize: 12, color: "#605e5c" }}>
                        {category.services.length}
                      </span>
                    </div>

                    {isExpanded && (
                      <div style={{ paddingLeft: 20 }}>
                        {category.services.map(service => (
                          <label
                            key={service.key}
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: 8,
                              padding: "6px 8px",
                              cursor: "pointer",
                              fontSize: 14,
                              color: "#323130",
                              borderRadius: 2,
                            }}
                            onMouseEnter={e => e.currentTarget.style.background = "#f3f2f1"}
                            onMouseLeave={e => e.currentTarget.style.background = "transparent"}
                          >
                            <input
                              type="checkbox"
                              checked={props.serviceFilter.has(service.key)}
                              onChange={e => {
                                const next = new Set(props.serviceFilter);
                                if (e.target.checked) next.add(service.key);
                                else next.delete(service.key);
                                props.onServiceFilterChange(next);
                              }}
                              style={{ cursor: "pointer", margin: 0 }}
                            />
                            {service.label}
                          </label>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Validation Source Filter */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: "#323130", textTransform: "uppercase", letterSpacing: "0.5px" }}>Validation Source</h3>
              <BulkSelectionButtons
                onSelectAll={() => {
                  const allSources = props.validationSourceOptions.map(vs => vs.key);
                  props.onValidationSourceFilterChange(new Set(allSources));
                }}
                onUncheckAll={() => {
                  props.onValidationSourceFilterChange(new Set());
                }}
              />
            </div>
            <div
              style={{
                maxHeight: 150,
                overflowY: "auto",
                border: "1px solid #8a8886",
                borderRadius: 2,
                padding: "4px 8px",
                background: "#fff",
              }}
            >
              {props.validationSourceOptions.map(vs => (
                <label
                  key={vs.key}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "6px 4px",
                    cursor: "pointer",
                    fontSize: 14,
                    color: "#323130",
                    borderRadius: 2,
                  }}
                  onMouseEnter={e => e.currentTarget.style.background = "#f3f2f1"}
                  onMouseLeave={e => e.currentTarget.style.background = "transparent"}
                >
                  <input
                    type="checkbox"
                    checked={props.validationSourceFilter.has(vs.key)}
                    onChange={e => {
                      const next = new Set(props.validationSourceFilter);
                      if (e.target.checked) next.add(vs.key);
                      else next.delete(vs.key);
                      props.onValidationSourceFilterChange(next);
                    }}
                    style={{ cursor: "pointer" }}
                  />
                  {vs.label}
                </label>
              ))}
            </div>
          </div>

          <button
            onClick={props.onToggleLegend}
            title="Show/hide visual legend"
            style={{
              ...secondaryButton,
              width: "100%",
              background: props.showLegend ? "rgba(0, 120, 212, 0.1)" : "transparent",
              borderColor: props.showLegend ? "#0078d4" : "#8a8886",
            }}
            onMouseEnter={e => {
              if (!props.showLegend) {
                e.currentTarget.style.background = "rgba(0, 120, 212, 0.05)";
                e.currentTarget.style.borderColor = "#0078d4";
              }
            }}
            onMouseLeave={e => {
              if (!props.showLegend) {
                e.currentTarget.style.background = "transparent";
                e.currentTarget.style.borderColor = "#8a8886";
              }
            }}
          >
            {props.showLegend ? "Hide" : "Show"} Legend
          </button>
        </>
      )}

      {showMappingModal && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(15, 23, 42, 0.35)",
            backdropFilter: "blur(2px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 10000,
            padding: 20,
          }}
          onKeyDownCapture={e => {
            if (e.key === "Escape") {
              e.preventDefault();
              e.stopPropagation();
            }
          }}
        >
          <div
            style={{
              width: "min(920px, 94vw)",
              maxHeight: "80vh",
              background: "#ffffff",
              color: "#111827",
              border: "1px solid #d1d5db",
              borderRadius: 12,
              boxShadow: "0 24px 48px -20px rgba(15, 23, 42, 0.35)",
              display: "flex",
              flexDirection: "column",
              position: "relative",
            }}
            onClick={e => e.stopPropagation()}
          >
            <CloseIconButton
              onClick={closeMappingModal}
              title="Close"
              disabled={props.mappingInProgress}
              absolute
              top={10}
              right={10}
            />

            <div style={{ padding: "20px 20px 0", marginBottom: 12, paddingRight: 56, display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
              <div style={{ minWidth: 0 }}>
                <h2 style={{ marginTop: 0, marginBottom: 6, fontSize: 22, fontWeight: 700, color: "#111827" }}>Map New Workload</h2>
              </div>
            </div>

            <div
              style={{
                margin: "0 20px 16px",
                display: "flex",
                gap: 20,
                flexWrap: "wrap",
                borderBottom: "1px solid #e5e7eb",
              }}
            >
              <button
                type="button"
                onClick={() => setActiveMappingTab("essentials")}
                style={{
                  padding: "10px 2px 12px",
                  marginBottom: -1,
                  borderRadius: 0,
                  border: "none",
                  borderBottom: activeMappingTab === "essentials" ? "3px solid #4f46e5" : "3px solid transparent",
                  background: "transparent",
                  color: "#111827",
                  fontWeight: 700,
                  fontSize: 15,
                  cursor: "pointer",
                }}
              >
                Azure Resources
              </button>
              <button
                type="button"
                onClick={() => setActiveMappingTab("terraform")}
                style={{
                  padding: "10px 2px 12px",
                  marginBottom: -1,
                  borderRadius: 0,
                  border: "none",
                  borderBottom: activeMappingTab === "terraform" ? "3px solid #4f46e5" : "3px solid transparent",
                  background: "transparent",
                  color: "#111827",
                  fontWeight: 700,
                  fontSize: 15,
                  cursor: "pointer",
                }}
              >
                Terraform Scripts
              </button>
              <button
                type="button"
                onClick={() => setActiveMappingTab("progress")}
                style={{
                  padding: "10px 2px 12px",
                  marginBottom: -1,
                  borderRadius: 0,
                  border: "none",
                  borderBottom: activeMappingTab === "progress" ? "3px solid #4f46e5" : "3px solid transparent",
                  background: "transparent",
                  color: "#111827",
                  fontWeight: 700,
                  fontSize: 15,
                  cursor: "pointer",
                }}
              >
                Progress
              </button>
            </div>

            <div style={{ padding: "0 20px 20px", overflowY: "auto", display: "flex", flexDirection: "column", gap: 14 }}>
              {activeMappingTab === "essentials" ? (
                <div style={{ border: "1px solid #e5e7eb", borderRadius: 14, padding: 16, background: "#ffffff" }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "#111827", marginBottom: 6 }}>
                    Subscription
                  </div>
                  <select
                    value={selectedMappingSubscriptionId}
                    onChange={e => setSelectedMappingSubscriptionId(e.target.value)}
                    style={{
                      width: "100%",
                      background: "#fff",
                      color: "#111827",
                      border: "1px solid #d1d5db",
                      padding: "8px 10px",
                      borderRadius: 6,
                      fontSize: 14,
                      cursor: "pointer",
                      outline: "none",
                    }}
                  >
                    {props.availableSubscriptionsForMapping.length === 0 ? (
                      <option value="">No subscriptions available</option>
                    ) : (
                      props.availableSubscriptionsForMapping.map(sub => (
                        <option key={sub.id} value={sub.id}>
                          {sub.name} ({sub.state})
                        </option>
                      ))
                    )}
                  </select>

                  <div style={{ marginTop: 14 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                      <div style={{ fontSize: 13, fontWeight: 700, color: "#111827" }}>
                        Resource Groups
                      </div>
                      {!mappingResourceGroupsLoading && mappingResourceGroups.length > 0 && (
                        <BulkSelectionButtons
                          onSelectAll={() => setSelectedMappingResourceGroups(new Set(mappingResourceGroups))}
                          onUncheckAll={() => setSelectedMappingResourceGroups(new Set())}
                        />
                      )}
                    </div>

                    <div style={{ border: "1px solid #d1d5db", borderRadius: 8, background: "#fff", maxHeight: 180, overflowY: "auto", padding: "4px 8px" }}>
                      {mappingResourceGroupsLoading ? (
                        <div style={{ fontSize: 13, color: "#6b7280", padding: "8px 0" }}>Loading resource groups...</div>
                      ) : mappingResourceGroupsError ? (
                        <div style={{ fontSize: 13, color: "#d13438", padding: "8px 0" }}>{mappingResourceGroupsError}</div>
                      ) : mappingResourceGroups.length === 0 ? (
                        <div style={{ fontSize: 13, color: "#6b7280", padding: "8px 0" }}>No resource groups found. Mapping will include all resources in the subscription.</div>
                      ) : (
                        mappingResourceGroups.map(group => (
                          <label key={group} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 4px", cursor: "pointer" }}>
                            <input
                              type="checkbox"
                              checked={selectedMappingResourceGroups.has(group)}
                              onChange={e => {
                                const next = new Set(selectedMappingResourceGroups);
                                if (e.target.checked) next.add(group);
                                else next.delete(group);
                                setSelectedMappingResourceGroups(next);
                              }}
                              style={{ cursor: "pointer" }}
                            />
                            <span style={{ fontSize: 14, color: "#111827", wordBreak: "break-word" }}>{group}</span>
                          </label>
                        ))
                      )}
                    </div>
                  </div>

                  <div style={{ marginTop: 14 }}>
                    <div style={{ fontSize: 13, fontWeight: 700, color: "#111827", marginBottom: 6 }}>
                      Tags
                    </div>
                    <input
                      value={tagsInput}
                      onChange={e => setTagsInput(e.target.value)}
                      placeholder="environment=prod, owner=platform"
                      style={{
                        width: "100%",
                        background: "#fff",
                        color: "#111827",
                        border: "1px solid #d1d5db",
                        padding: "8px 10px",
                        borderRadius: 6,
                        fontSize: 14,
                        boxSizing: "border-box",
                      }}
                    />
                  </div>
                </div>
              ) : activeMappingTab === "terraform" ? (
                <div style={{ border: "1px solid #e5e7eb", borderRadius: 14, padding: 16, background: "#ffffff" }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "#111827", marginBottom: 6 }}>
                    Subscription Name
                  </div>
                  <input
                    value={terraformSubscriptionName}
                    onChange={e => setTerraformSubscriptionName(e.target.value)}
                    placeholder="e.g., My Terraform Deployment"
                    style={{
                      width: "100%",
                      background: "#fff",
                      color: "#111827",
                      border: "1px solid #d1d5db",
                      padding: "8px 10px",
                      borderRadius: 6,
                      fontSize: 14,
                      boxSizing: "border-box",
                    }}
                    disabled={terraformUploadLoading}
                  />

                  <div style={{ marginTop: 14 }}>
                    <div
                      style={{
                        border: "1px dashed #9ca3af",
                        borderRadius: 10,
                        background: "#f9fafb",
                        padding: 16,
                        cursor: terraformUploadLoading ? "not-allowed" : "pointer",
                        textAlign: "center",
                      }}
                      onDragOver={e => {
                        e.preventDefault();
                      }}
                      onDrop={e => {
                        e.preventDefault();
                        if (terraformUploadLoading) return;
                        handleTerraformFileSelection(Array.from(e.dataTransfer.files));
                      }}
                      onClick={() => {
                        if (!terraformUploadLoading) terraformFileInputRef.current?.click();
                      }}
                    >
                      <input
                        ref={terraformFileInputRef}
                        type="file"
                        multiple
                        accept=".tf,.json"
                        onChange={e => handleTerraformFileSelection(Array.from(e.target.files ?? []))}
                        style={{ display: "none" }}
                        disabled={terraformUploadLoading}
                      />
                      <div style={{ fontSize: 14, color: "#111827", fontWeight: 600 }}>Drag & drop Terraform files here</div>
                      <div style={{ fontSize: 12, color: "#6b7280", marginTop: 6 }}>or click to select .tf and .json files</div>
                    </div>
                  </div>

                  {terraformFiles.length > 0 && (
                    <div style={{ marginTop: 14, border: "1px solid #d1d5db", borderRadius: 8, background: "#fff", maxHeight: 180, overflowY: "auto", padding: "4px 8px" }}>
                      {terraformFiles.map(file => (
                        <div key={file.name} style={{ fontSize: 13, color: "#111827", padding: "6px 4px", wordBreak: "break-word" }}>
                          {file.name}
                        </div>
                      ))}
                    </div>
                  )}

                  {terraformUploadError && (
                    <div style={{ marginTop: 12, fontSize: 13, color: "#d13438" }}>
                      {terraformUploadError}
                    </div>
                  )}

                  {terraformUploadSuccess && (
                    <div style={{ marginTop: 12, fontSize: 13, color: "#107c10" }}>
                      {terraformUploadSuccess}
                    </div>
                  )}
                </div>
              ) : (
                <div style={{ border: "1px solid #e5e7eb", borderRadius: 14, padding: 16, background: "#ffffff" }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 8 }}>
                    <div style={{ fontSize: 13, fontWeight: 700, color: "#111827" }}>
                      {showRawMappingLog ? "Raw Log" : "Progress Log"}
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                      <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "#374151", cursor: "pointer" }}>
                        <input
                          type="checkbox"
                          checked={showRawMappingLog}
                          onChange={e => setShowRawMappingLog(e.target.checked)}
                          style={{ cursor: "pointer" }}
                        />
                        Show raw log
                      </label>
                      <CopyToClipboardButton text={mappingDisplayedLogText} title="Copy log" ariaLabel="Copy progress log" />
                    </div>
                  </div>
                  <textarea
                    ref={mappingProgressTextareaRef}
                    readOnly
                    value={mappingDisplayedLogText}
                    style={{
                      width: "100%",
                      minHeight: 220,
                      maxHeight: 340,
                      background: "#f9fafb",
                      color: "#111827",
                      border: "1px solid #d1d5db",
                      borderRadius: 8,
                      padding: "10px 12px",
                      fontSize: 13,
                      lineHeight: 1.5,
                      fontFamily: "Consolas, Menlo, Monaco, monospace",
                      boxSizing: "border-box",
                      resize: "vertical",
                    }}
                  />
                </div>
              )}

              {activeMappingTab === "progress" && shouldShowMappingStatus && (
                <div style={{ marginTop: 14, border: "1px solid #e5e7eb", borderRadius: 8, background: "#f9fafb", padding: "10px 12px" }}>
                  {props.mappingError && (
                    <div style={{ fontSize: 13, color: "#d13438", marginBottom: props.mappingAuthRequired ? 8 : 0 }}>
                      {props.mappingError}
                    </div>
                  )}
                  {props.mappingAuthRequired && (
                    <a
                      href="https://learn.microsoft.com/cli/azure/authenticate-azure-cli"
                      target="_blank"
                      rel="noreferrer"
                      style={{ fontSize: 13, color: "#0078d4", textDecoration: "none" }}
                    >
                      Connect Azure (az login)
                    </a>
                  )}
                  {(mappingStatusMessage || typeof mappingProgressPercent === "number") && (
                    <div style={{ fontSize: 13, color: "#323130", marginTop: 6 }}>
                      {typeof mappingProgressPercent === "number" ? `Progress: ${mappingProgressPercent}%` : ""}
                      {mappingStatusMessage && typeof mappingProgressPercent === "number" ? " · " : ""}
                      {mappingStatusMessage}
                    </div>
                  )}
                </div>
              )}
            </div>

            <div style={{ padding: "0 20px 16px", display: "flex", justifyContent: "flex-end", gap: 8 }}>
              {activeMappingTab === "terraform" ? (
                <button
                  onClick={handleTerraformUpload}
                  disabled={terraformUploadLoading || terraformFiles.length === 0}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "8px 12px",
                    background: terraformUploadLoading || terraformFiles.length === 0 ? "#9ca3af" : "#0078d4",
                    color: "#fff",
                    border: "1px solid #0078d4",
                    borderRadius: 6,
                    textDecoration: "none",
                    fontWeight: 600,
                    fontSize: 13,
                    cursor: terraformUploadLoading || terraformFiles.length === 0 ? "not-allowed" : "pointer",
                  }}
                >
                  {terraformUploadLoading ? "Uploading..." : "Upload & Analyze"}
                </button>
              ) : (
                <button
                  onClick={handleStartMapping}
                  disabled={props.mappingInProgress || !selectedMappingSubscriptionId}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "8px 12px",
                    background: props.mappingInProgress || !selectedMappingSubscriptionId ? "#9ca3af" : "#0078d4",
                    color: "#fff",
                    border: "1px solid #0078d4",
                    borderRadius: 6,
                    textDecoration: "none",
                    fontWeight: 600,
                    fontSize: 13,
                    cursor: props.mappingInProgress || !selectedMappingSubscriptionId ? "not-allowed" : "pointer",
                  }}
                >
                  {props.mappingInProgress ? "Mapping..." : "Start Mapping"}
                </button>
              )}

              <button
                onClick={closeMappingModal}
                disabled={props.mappingInProgress || terraformUploadLoading}
                style={props.mappingInProgress || terraformUploadLoading ? disabledButton : secondaryButton}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default WorkloadSidebar;
