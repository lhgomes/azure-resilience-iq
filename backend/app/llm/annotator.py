import json
import logging
import os
from textwrap import dedent
from time import sleep
from typing import Any, Dict

from azure.identity import DefaultAzureCredential
from openai import AzureOpenAI
from pydantic import ValidationError

from .models import LLMAnnotations
from .summarizer import summarize_graph_for_llm
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

        - confidence:
            Numeric value between 0 and 1 reflecting certainty of the annotation.

        - reason:
            One short sentence explaining the classification.

        2) Optionally suggest missing relationships BETWEEN EXISTING NODES ONLY:
        - Prefer returning no suggested edges over low-confidence suggestions.
        - Only suggest an edge when confidence ≥ 0.5.
        - Use status "proposed" only.
        - Never assert authoritative relationships.

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
    except Exception:
        LOGGER.exception("Failed to build LLM-safe summary")
        return LLMAnnotations(nodes=[], edges=[])

    if not _use_real_llm():
        LOGGER.info("LLM annotations disabled; returning no advisory data")
        return LLMAnnotations(nodes=[], edges=[])

    # Check config before attempting request
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    if not endpoint or not deployment:
        LOGGER.warning(
            "USE_REAL_LLM=true but AZURE_OPENAI_ENDPOINT or AZURE_OPENAI_DEPLOYMENT not set; "
            "LLM annotation skipped. Set environment variables to enable."
        )
        return LLMAnnotations(nodes=[], edges=[])

    LOGGER.info("Starting Azure OpenAI annotation request")

    try:
        raw = _call_azure_openai(summary)
    except Exception:
        LOGGER.exception("Azure OpenAI annotation request failed; falling back to empty annotations")
        return LLMAnnotations(nodes=[], edges=[])

    try:
        annotations = _validate_and_filter_annotations(raw)
        annotations = _calculate_criticality_weights(annotations)
    except Exception:
        LOGGER.exception("LLM response failed final validation; returning empty annotations")
        return LLMAnnotations(nodes=[], edges=[])

    LOGGER.info("Azure OpenAI annotation request succeeded; %d nodes, %d edge suggestions", len(annotations.nodes), len(annotations.edges))
    return annotations


def llm_config_enabled() -> bool:
    """Check if LLM annotations are enabled from app config."""
    try:
        settings = get_settings()
        return settings.is_annotation_enabled()
    except RuntimeError:
        # Fallback to environment variable if settings not initialized
        flag = os.getenv("LLM_ANNOTATION_ENABLED", "true").lower().strip()
        return flag in {"1", "true", "yes", "on"}


def _use_real_llm() -> bool:
    """Check if real LLM should be used from app config."""
    try:
        settings = get_settings()
        return settings.use_real_llm()
    except RuntimeError:
        # Fallback to environment variable if settings not initialized
        flag = os.getenv("USE_REAL_LLM", "false").lower().strip()
        return flag in {"1", "true", "yes", "on"}


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


def _validate_and_filter_annotations(raw: Dict[str, Any]) -> LLMAnnotations:
    """
    Enforce contract on LLM response: validate priority and layer values.
    Drop invalid items with WARN logs instead of failing the whole batch.
    Auto-correct layer values to valid range (0-2).
    Auto-abbreviate long category names (>20 chars).
    """
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
            node_id = item.get("node_id")
            ann = item.get("annotations") or {}
            priority = ann.get("priority")
            layer = ann.get("layer")
            category = ann.get("azure_service_category")

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
            source = item.get("source")
            target = item.get("target")
            relationship = item.get("relationship")
            if not source or not target:
                LOGGER.warning("Dropping edge suggestion: missing source or target")
                continue
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

    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-05-01-preview")
    timeout_seconds = int(os.getenv("AZURE_OPENAI_TIMEOUT_SECONDS", "60"))
    max_attempts = int(os.getenv("AZURE_OPENAI_MAX_ATTEMPTS", "2"))
    max_tokens = int(os.getenv("AZURE_OPENAI_MAX_TOKENS", "6000"))

    if not endpoint or not deployment:
        raise RuntimeError("Azure OpenAI endpoint or deployment not configured")

    # Prefer API key if provided; otherwise use AAD.
    api_key = os.getenv("AZURE_OPENAI_KEY")
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
