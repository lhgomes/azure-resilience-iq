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

LOGGER = logging.getLogger(__name__)

# Prompt for the production LLM call.
# Sections:
# - Role & safety rails: senior Azure architect, respect existing topology, advisory only.
# - Task asks: display names, layers (L0-L2 mapped to ints), priority, hide flag, optional edge proposals.
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
        - L0 (0): User-facing or core workload components whose failure directly impacts
        customers or primary business functionality (e.g., AKS, App Service, primary databases).
        - L1 (1): Network and platform boundaries enabling or isolating workloads
        (e.g., VNets, subnets, private endpoints, load balancers).
        - L2 (2): Implementation details or per-instance artifacts that add noise at
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
            Azure architecture icon category corresponding to the official Azure
            Architecture Icons taxonomy, such as:
            Compute, Containers, Networking, Databases, Storage, Identity,
            Integration, Security, ManagementAndGovernance.
            If unsure, choose the broader category.

        - azure_service_name:
            Official Azure service name as shown in Azure Portal.
            This value will be used by the application to deterministically map
            to the correct Azure architecture icon.
            Do NOT return icon filenames, URLs, or SVG names.

        - layer:
            Integer value: 0, 1, or 2, following the layering guidance above.

        - priority:
            One of: critical | important | supporting.
            Must align with the criticality_score.

        - criticality_score:
            Integer 1–10 based on business impact, blast radius, and dependency count.

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

        ICON CLASSIFICATION RULES
        -------------------------
        - Do NOT invent new Azure services or categories.
        - Use official Azure Portal naming conventions.
        - Do NOT output icon filenames, SVG names, URLs, or asset paths.
        - These fields are used for deterministic icon mapping by the application.

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
            "from_id": "<existing node id>",
            "to_id": "<existing node id>",
            "relationship": "<short verb phrase>",
            "status": "proposed",
            "confidence": 0.6,
            "reason": "...",
            "source": "llm"
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
    except Exception:
        LOGGER.exception("LLM response failed final validation; returning empty annotations")
        return LLMAnnotations(nodes=[], edges=[])

    LOGGER.info("Azure OpenAI annotation request succeeded; %d nodes, %d edge suggestions", len(annotations.nodes), len(annotations.edges))
    return annotations


def llm_config_enabled() -> bool:
    flag = os.getenv("LLM_ANNOTATION_ENABLED", "true").lower().strip()
    return flag in {"1", "true", "yes", "on"}


def _use_real_llm() -> bool:
    flag = os.getenv("USE_REAL_LLM", "false").lower().strip()
    return flag in {"1", "true", "yes", "on"}


def _validate_and_filter_annotations(raw: Dict[str, Any]) -> LLMAnnotations:
    """
    Enforce contract on LLM response: validate priority and layer values.
    Drop invalid items with WARN logs instead of failing the whole batch.
    """
    allowed_priorities = {"critical", "important", "supporting"}
    allowed_layers = {0, 1, 2}

    valid_nodes = []
    for item in raw.get("nodes") or []:
        try:
            node_id = item.get("node_id")
            ann = item.get("annotations") or {}
            priority = ann.get("priority")
            layer = ann.get("layer")

            # Validate priority
            if priority and priority not in allowed_priorities:
                LOGGER.warning(
                    "Dropping node annotation for %s: invalid priority '%s' (allowed: %s)",
                    node_id, priority, allowed_priorities
                )
                continue

            # Validate layer
            if layer is not None and layer not in allowed_layers:
                LOGGER.warning(
                    "Dropping node annotation for %s: invalid layer %s (allowed: %s)",
                    node_id, layer, allowed_layers
                )
                continue

            valid_nodes.append(item)
        except Exception:
            LOGGER.warning("Skipping malformed node annotation item", exc_info=True)
            continue

    valid_edges = []
    for item in raw.get("edges") or []:
        try:
            from_id = item.get("from_id")
            to_id = item.get("to_id")
            if not from_id or not to_id:
                LOGGER.warning("Dropping edge suggestion: missing from_id or to_id")
                continue
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
