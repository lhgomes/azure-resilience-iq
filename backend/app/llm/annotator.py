import json
import logging
from textwrap import dedent
from time import sleep
from typing import Any, Dict

from azure.identity import DefaultAzureCredential
from openai import AzureOpenAI
from pydantic import ValidationError

from .models import LLMAnnotations
from .summarizer import summarize_graph_for_llm
from .batcher import BatchPartitioner, merge_batch_annotations
from app.graph.builder import edge_id
from app.settings import get_settings

LOGGER = logging.getLogger(__name__)

# Prompt for the production LLM call.
# Sections:
# - Role & safety rails: senior Azure architect, respect existing topology, advisory only.
# - Task asks: display names, layers (L1-L3 mapped to ints), priority, hide flag, optional edge proposals.
# - Output contract: JSON only, deterministic friendly for low-temperature usage.
ARCHITECT_ANNOTATION_PROMPT: str = dedent(
        """
        You are a senior Azure solution architect reviewing an Azure workload graph.

        SYSTEM GUARDRAILS
        -----------------
        - The provided topology (nodes and edges) is already correct and authoritative.
        - Do NOT invent new resources.
        - Do NOT remove, alter, or assert relationships.
        - All outputs are advisory suggestions only and must remain non-authoritative.
        - Prefer conservative classification when uncertain.

        WORKING STYLE
        -------------
        - Be concise, deterministic, and factual.
        - Avoid creative wording.
        - Use official Azure terminology as shown in the Azure Portal.
        - When unsure, choose the broader, safer classification.

        IDENTIFIERS (READ CAREFULLY)
        ----------------------------
        - Each node includes both a canonical Azure id ("id") and a compact identifier ("short_id"), a deterministic UUIDv5 derived from the Azure id.
        - Use short_id for ALL references in your output: node_id, source, and target. Do not invent ids.
        - The connections array already uses short_id values.

        LAYERING GUIDANCE (apply consistently)
        --------------------------------------
        - L1 (1): User-facing or core workload components whose failure directly impacts
        customers or primary business functionality (e.g., AKS, App Service, primary databases).
        - L2 (2): Network and platform boundaries enabling or isolating workloads
        (e.g., VNets, subnets, private endpoints, load balancers).
        - L3 (3): Implementation details or per-instance artifacts that add noise at
        architecture level (e.g., NICs, IP configurations, VM extensions).

        PRIORITY ↔ CRITICALITY SCORE MAPPING (MUST MATCH)
        -------------------------------------------------
        - critical   → score 8–10
        - important  → score 5–7
        - supporting → score 1–4

        TASKS
        -----
        1) For EVERY existing node, suggest the following annotations:

        - display_name:
            Short, human-friendly label suitable for an architecture diagram.
            If unsure, fall back to the existing resource name.

        - azure_service_category:
            Azure architecture category corresponding to the official Azure
            Architecture taxonomy, such as:
            Compute, Containers, Networking, Databases, Storage, Identity,
            Integration, Security, Management And Governance.
            
            CRITICAL: Category names MUST be 20 characters or less.
            Use these exact abbreviations for long names:
            - "Mngmt & Governance" (NOT "Management And Governance")
            - "AI/ML" (NOT "AI + Machine Learning" or "Artificial Intelligence")
            
            If unsure, choose the broader category.

        - azure_service_name:
            Official Azure service name as shown in Azure Portal.
            This value will be used by the application to deterministically map
            to the correct Azure architecture icon.

        - layer:
            Integer value: 0, 1, or 2, following the layering guidance above.

        - priority:
            One of: critical | important | supporting.
            Must align with the criticality_score.

        - criticality_score:
            Integer 1–10 based on business impact, blast radius, and dependency count.
            
            SCORING GUIDELINES (use node's connection_count field)
            -------------------------------------------------------
            - Isolated nodes (connection_count = 0): default to 1–3 unless the
              resource type itself is inherently critical (e.g., standalone key vault,
              storage account with important data, compliance resources).
            - Nodes with few dependencies (connection_count 1–2): typically 3–5.
            - Nodes with moderate dependencies (connection_count 3–5): typically 5–7.
            - Nodes with many dependencies or high fan-out (connection_count 6+): typically 7–10.
            - Adjust based on resource type criticality (e.g., AKS, App Service, databases
              are typically higher; NICs, IP configs are typically lower).
            
            If the node metadata includes criticality_override (user-authored), treat that
            as the baseline score and only adjust when there is strong evidence that a
            materially different score is warranted. Make the rationale explicit when
            diverging from the override.
            
            When assessing blast radius and dependency impact, treat user-created or
            accepted edges (source_kind "manual" or status "accepted") as authoritative.
            Use these user relationships to raise or lower criticality based on how they
            change the node's dependencies and exposure.

                RELATIONSHIP PROVENANCE
                -----------------------
                - Some edges may have source_kind "manual" (user-created) and/or status "accepted";
                    treat these as authoritative user intent when considering dependency/blast radius
                    for criticality. Do NOT propose removing or contradicting them.
                
                MULTI-SOURCE SIGNAL CONFIDENCE
                ------------------------------
                - Edges may include multi_source_signals with aggregated_confidence scores (0–1).
                - Higher aggregated_confidence (≥0.9) indicates the relationship was detected by
                  multiple independent methods (ARM topology, DNS records, Flow Logs, App Insights, etc.).
                - Use this signal confidence as supporting evidence for relationship criticality:
                  + High confidence (≥0.9) → relationship is highly reliable; elevate criticality
                  + Medium confidence (0.7–0.89) → relationship is well-supported; use as-is
                  + Lower confidence (<0.7) → relationship may be incidental; apply caution
                - Never contradict user-created edges, but confidence levels may inform whether
                  critical-path edges are truly business-critical vs. infrastructure artifacts.

        - hide_by_default:
            true ONLY if the resource is low-signal or noisy at architecture level.
            Do NOT set hide_by_default=true for any resource that is on a critical path
            or directly feeds/protects a component with criticality_score ≥ 3 (e.g.,
            storage backing a VM, disks, NICs, subnets, NSGs, PIPs, load balancers
            for active workloads). Keep those visible.

        - confidence:
            Numeric value between 0 and 1 reflecting certainty of the annotation.

        - reason:
            One short sentence explaining the classification.

        2) Optionally suggest missing relationships BETWEEN EXISTING NODES ONLY:
        - Prefer returning no suggested edges over low-confidence suggestions.
        - Only suggest an edge when confidence ≥ 0.5.
        - Use status "proposed" only.
        - Never assert authoritative relationships.

        IMPORTANT: Use the provided short_id values (not the long Azure id) for node_id, source, and target in your output.

        OUTPUT FORMAT (JSON ONLY)
        -------------------------
        {
        "nodes": [
            {
            "node_id": "<existing node id>",
            "annotations": {
                "display_name": "...",
                "azure_service_category": "...",
                "azure_service_name": "...",
                "layer": 0,
                "priority": "critical",
                "criticality_score": 8,
                "hide_by_default": false,
                "confidence": 0.85,
                "reason": "..."
            }
            }
        ],
        "edges": [
            {
            "source": "<existing node id>",
            "target": "<existing node id>",
            "relationship": "<short verb phrase>",
            "status": "proposed",
            "confidence": 0.6,
            "reason": "...",
            "origin": "llm"
            }
        ]
        }

        Return JSON only.
        Do NOT include prose, markdown, explanations, or commentary.
        
        """
).strip()


def annotate_graph(snapshot: Dict[str, Any]) -> LLMAnnotations:
    try:
        summary = summarize_graph_for_llm(snapshot)
        id_lookup = {
            n.get("short_id") or n.get("id"): n.get("id")
            for n in summary.get("nodes", [])
            if (n.get("short_id") or n.get("id")) and n.get("id")
        }
        # Accept canonical ids directly as a no-op mapping to keep backward compatibility
        for canonical_id in list(id_lookup.values()):
            id_lookup.setdefault(canonical_id, canonical_id)
    except Exception:
        LOGGER.exception("Failed to build LLM-safe summary")
        return LLMAnnotations(nodes=[], edges=[])

    if not _use_real_llm():
        LOGGER.info("LLM annotations disabled; returning no advisory data")
        return LLMAnnotations(nodes=[], edges=[])

    settings = get_settings()
    aoai_cfg = settings.get_azure_openai_config()

    # Check config before attempting request
    endpoint = aoai_cfg["endpoint"]
    deployment = aoai_cfg["deployment"]
    if not endpoint or not deployment:
        LOGGER.warning(
            "LLM enabled but azure_openai.endpoint or azure_openai.deployment not set; "
            "LLM annotation skipped."
        )
        return LLMAnnotations(nodes=[], edges=[])

    LOGGER.info("Starting Azure OpenAI annotation request")

    # Decide: batch or single call based on graph size
    nodes = summary.get("nodes", [])
    edges = summary.get("edges", [])
    batch_cfg = settings.get_llm_batching_config()
    batch_threshold = batch_cfg["batch_threshold"]
    max_nodes_per_batch = batch_cfg["max_nodes_per_batch"]

    if len(nodes) > batch_threshold:
        LOGGER.info("Graph size %d exceeds batch threshold %d; using batched annotation", len(nodes), batch_threshold)
        annotations = _annotate_batched(summary, id_lookup, max_nodes_per_batch)
    else:
        LOGGER.info("Graph size %d within single-call threshold; using direct annotation", len(nodes))
        try:
            raw = _call_azure_openai(summary)
        except Exception:
            LOGGER.exception("Azure OpenAI annotation request failed; falling back to empty annotations")
            return LLMAnnotations(nodes=[], edges=[])

        try:
            annotations = _validate_and_filter_annotations(raw, id_lookup)
        except Exception:
            LOGGER.exception("LLM response failed validation; returning empty annotations")
            return LLMAnnotations(nodes=[], edges=[])

    # Recompute criticality_weight over full merged set
    try:
        annotations = _calculate_criticality_weights(annotations)
    except Exception:
        LOGGER.exception("Failed to calculate criticality weights")
        return LLMAnnotations(nodes=[], edges=[])

    LOGGER.info("Azure OpenAI annotation request succeeded; %d nodes, %d edge suggestions", len(annotations.nodes), len(annotations.edges))
    return annotations


def llm_config_enabled() -> bool:
    """Check if LLM annotations are enabled from app config."""
    settings = get_settings()
    return settings.is_annotation_enabled()

def _annotate_batched(
    summary: Dict[str, Any],
    id_lookup: Dict[str, str],
    max_nodes_per_batch: int,
) -> LLMAnnotations:
    """
    Annotate a large graph by partitioning into independent batches.

    Each batch includes global node features (connection_count, overrides, manual edges)
    and internal edges. Batch calls are stateless. Results are merged and weights
    are recomputed over the full set.
    """
    nodes = summary.get("nodes", [])
    edges = summary.get("edges", [])

    # Partition into batches
    partitioner = BatchPartitioner(nodes, edges, max_nodes_per_batch=max_nodes_per_batch)
    batches = partitioner.partition()
    LOGGER.info("Partitioned %d nodes into %d batches", len(nodes), len(batches))

    batch_results = []

    for batch_idx, batch in enumerate(batches, start=1):
        LOGGER.info("Processing batch %d/%d (%d nodes, %d edges)", batch_idx, len(batches), len(batch["nodes"]), len(batch["edges"]))

        # Build a minimal batch summary with global features
        batch_summary = {
            "nodes": batch["nodes"],
            "edges": batch["edges"],
            "batch_context": batch["batch_context"],
        }

        try:
            raw = _call_azure_openai(batch_summary)
        except Exception:
            LOGGER.exception("Batch %d annotation failed; skipping", batch_idx)
            continue

        try:
            batch_annotations = _validate_and_filter_annotations(raw, id_lookup)
            batch_results.append(batch_annotations)
        except Exception:
            LOGGER.exception("Batch %d validation failed; skipping", batch_idx)
            continue

    if not batch_results:
        LOGGER.warning("All batches failed; returning empty annotations")
        return LLMAnnotations(nodes=[], edges=[])

    # Merge batch results
    merged = merge_batch_annotations(batch_results)
    LOGGER.info("Merged %d batch results; %d nodes, %d edges", len(batch_results), len(merged.nodes), len(merged.edges))

    return merged


def _use_real_llm() -> bool:
    """Check if real LLM should be used from app config."""
    settings = get_settings()
    return settings.use_real_llm()


def _calculate_criticality_weights(annotations: LLMAnnotations) -> LLMAnnotations:
    """
    Calculate criticality_weight for each node based on criticality_score.
    Excludes nodes with hide_by_default=true.
    Normalizes weights to sum to exactly 100.
    """
    # Collect nodes with scores that should contribute to weight
    weighted_nodes = []
    for node_ann in annotations.nodes:
        ann = node_ann.annotations
        if ann.hide_by_default:
            # Hidden nodes get 0 weight
            ann.criticality_weight = 0.0
        elif ann.criticality_score is not None:
            weighted_nodes.append(node_ann)
        else:
            # No score means no weight
            ann.criticality_weight = None

    # Calculate total score for normalization
    total_score = sum(node.annotations.criticality_score for node in weighted_nodes)

    if total_score > 0:
        # Distribute 100 points proportionally
        for node_ann in weighted_nodes:
            raw_weight = (node_ann.annotations.criticality_score / total_score) * 100.0
            node_ann.annotations.criticality_weight = round(raw_weight, 2)

        # Adjust for rounding errors to ensure exact sum of 100
        actual_sum = sum(
            node.annotations.criticality_weight
            for node in weighted_nodes
            if node.annotations.criticality_weight is not None
        )
        if actual_sum != 100.0 and weighted_nodes:
            # Add the difference to the highest-scored node
            highest_node = max(weighted_nodes, key=lambda n: n.annotations.criticality_score)
            adjustment = 100.0 - actual_sum
            highest_node.annotations.criticality_weight += adjustment
            highest_node.annotations.criticality_weight = round(
                highest_node.annotations.criticality_weight, 2
            )

    return annotations


def _validate_and_filter_annotations(raw: Dict[str, Any], id_lookup: Dict[str, str]) -> LLMAnnotations:
    """
    Enforce contract on LLM response: validate priority and layer values.
    Drop invalid items with WARN logs instead of failing the whole batch.
    Auto-correct layer values to valid range (0-2).
    Auto-abbreviate long category names (>20 chars).
    """
    id_lookup = id_lookup or {}

    def resolve_id(maybe_short_id: Any) -> Any:
        if maybe_short_id is None:
            return None
        if not isinstance(maybe_short_id, str):
            return maybe_short_id
        return id_lookup.get(maybe_short_id, maybe_short_id)
    allowed_priorities = {"critical", "important", "supporting"}
    allowed_layers = {0, 1, 2}
    
    # Category abbreviation mapping
    category_abbreviations = {
        "Management And Governance": "Mngmt & Governance",
        "Management and Governance": "Mngmt & Governance",
        "AI + Machine Learning": "AI/ML",
        "Artificial Intelligence": "AI/ML",
    }

    valid_nodes = []
    for item in raw.get("nodes") or []:
        try:
            node_id = resolve_id(item.get("node_id"))
            ann = item.get("annotations") or {}
            priority = ann.get("priority")
            layer = ann.get("layer")
            category = ann.get("azure_service_category")

            if not node_id:
                LOGGER.warning("Dropping node annotation with missing node_id")
                continue
            item["node_id"] = node_id

            # Validate priority
            if priority and priority not in allowed_priorities:
                LOGGER.warning(
                    "Dropping node annotation for %s: invalid priority '%s' (allowed: %s)",
                    node_id, priority, allowed_priorities
                )
                continue

            # Auto-correct layer: clamp to valid range
            if layer is not None and layer not in allowed_layers:
                if isinstance(layer, (int, float)):
                    # Clamp to valid range
                    corrected_layer = max(0, min(2, int(layer)))
                    LOGGER.warning(
                        "Auto-correcting layer for %s: %s → %s",
                        node_id, layer, corrected_layer
                    )
                    ann["layer"] = corrected_layer
                else:
                    LOGGER.warning(
                        "Dropping node annotation for %s: invalid layer %s (allowed: %s)",
                        node_id, layer, allowed_layers
                    )
                    continue

            # Auto-abbreviate long category names
            if category:
                if category in category_abbreviations:
                    original = category
                    ann["azure_service_category"] = category_abbreviations[category]
                    LOGGER.info(
                        "Auto-abbreviated category for %s: '%s' → '%s'",
                        node_id, original, ann["azure_service_category"]
                    )
                elif len(category) > 20:
                    LOGGER.warning(
                        "Category name too long for %s: '%s' (%d chars). Consider adding abbreviation.",
                        node_id, category, len(category)
                    )

            valid_nodes.append(item)
        except Exception:
            LOGGER.warning("Skipping malformed node annotation item", exc_info=True)
            continue

    valid_edges = []
    for item in raw.get("edges") or []:
        try:
            source = resolve_id(item.get("source"))
            target = resolve_id(item.get("target"))
            relationship = item.get("relationship")
            if not source or not target:
                LOGGER.warning("Dropping edge suggestion: missing source or target")
                continue
            item["source"] = source
            item["target"] = target
            # Add computed edge ID if not present
            if "id" not in item:
                item["id"] = edge_id(source, target, relationship or "")
            valid_edges.append(item)
        except Exception:
            LOGGER.warning("Skipping malformed edge suggestion item", exc_info=True)
            continue

    return LLMAnnotations(nodes=valid_nodes, edges=valid_edges)


def _call_azure_openai(summary: Dict[str, Any]) -> Dict[str, Any]:
    """
    Invoke Azure OpenAI with the architect prompt and return parsed JSON.
    The graph summary is treated as authoritative input; the LLM is advisory only.
    """

    aoai_cfg = get_settings().get_azure_openai_config()

    endpoint = aoai_cfg["endpoint"]
    deployment = aoai_cfg["deployment"]
    api_version = aoai_cfg["api_version"]
    timeout_seconds = aoai_cfg["timeout_seconds"]
    max_attempts = aoai_cfg["max_attempts"]
    max_tokens = aoai_cfg["max_tokens"]

    if not endpoint or not deployment:
        raise RuntimeError("Azure OpenAI endpoint or deployment not configured")

    # Prefer API key if provided; otherwise use AAD.
    api_key = aoai_cfg.get("api_key")
    if api_key:
        client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            timeout=timeout_seconds,
        )
    else:
        credential = DefaultAzureCredential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")
        client = AzureOpenAI(
            azure_endpoint=endpoint,
            azure_ad_token=token.token,
            api_version=api_version,
            timeout=timeout_seconds,
        )

    messages = [
        {"role": "system", "content": ARCHITECT_ANNOTATION_PROMPT},
        {
            "role": "user",
            "content": f"Graph summary (authoritative):\n{json.dumps(summary, ensure_ascii=False)}",
        },
    ]

    for attempt in range(1, max_attempts + 1):
        try:
            completion = client.chat.completions.create(
                model=deployment,
                messages=messages,
                temperature=0.1,  # low temperature to stay deterministic
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                timeout=timeout_seconds,
            )

            content = completion.choices[0].message.content
            if not content:
                raise ValueError("Empty response from LLM")

            # Log token usage for visibility
            usage = getattr(completion, "usage", None) or {}
            prompt_tokens = getattr(usage, "prompt_tokens", None) if hasattr(usage, "prompt_tokens") else usage.get("prompt_tokens") if isinstance(usage, dict) else None
            completion_tokens = getattr(usage, "completion_tokens", None) if hasattr(usage, "completion_tokens") else usage.get("completion_tokens") if isinstance(usage, dict) else None
            total_tokens = getattr(usage, "total_tokens", None) if hasattr(usage, "total_tokens") else usage.get("total_tokens") if isinstance(usage, dict) else None
            LOGGER.info(
                "Azure OpenAI usage: prompt_tokens=%s completion_tokens=%s total_tokens=%s",
                prompt_tokens,
                completion_tokens,
                total_tokens,
            )

            # Check if response was truncated
            finish_reason = completion.choices[0].finish_reason
            if finish_reason == "length":
                LOGGER.warning("LLM response truncated; increase max_tokens")
                raise ValueError("Response truncated by token limit")

            return json.loads(content)
        except json.JSONDecodeError as e:
            LOGGER.warning("Azure OpenAI attempt %s/%s failed: invalid JSON - %s", attempt, max_attempts, e)
            if attempt == max_attempts:
                raise
            sleep(1)  # brief backoff before retry
        except Exception as e:
            LOGGER.warning("Azure OpenAI attempt %s/%s failed: %s", attempt, max_attempts, e)
            if attempt == max_attempts:
                raise
            sleep(1)  # brief backoff before retry
