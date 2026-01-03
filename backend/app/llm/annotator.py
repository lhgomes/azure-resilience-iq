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

        System guardrails:
        - The provided topology (nodes and edges) is already correct and authoritative.
        - Do NOT invent new resources or relationships; do NOT alter existing relationships.
        - All outputs are advisory suggestions only and must remain non-authoritative.

        Working style:
        - Be concise, deterministic, and avoid creative wording.
        - Prefer short, human-friendly names and pragmatic rationale.
        - When uncertain, prefer conservative defaults over speculation.

        Layering guidance (apply consistently):
        - L0 (0): User-facing or core workload components whose failure directly impacts customers or primary business functionality (e.g., AKS clusters, App Services, primary databases).
        - L1 (1): Network and platform boundaries that enable or isolate workloads (e.g., VNets, subnets, private endpoints, load balancers).
        - L2 (2): Implementation details or per-instance artifacts that add noise at architecture level (e.g., NICs, IP configurations, VM extensions).

        Priority ↔ criticality_score mapping (must be consistent):
        - critical → score 8-10
        - important → score 5-7
        - supporting → score 1-4

        Tasks:
        1) For every existing node, suggest:
        - display_name: concise, human-friendly label (fallback to current name if unsure).
        - service_display_name: official Azure Portal service label (pluralized, e.g., "Virtual Machines", "Virtual networks").
            * If uncertain, derive conservatively from the ARM type path.
            * If still uncertain, return null rather than inventing a service name.
        - layer (integer): 0, 1, or 2 according to the guidance above.
        - priority: critical | important | supporting (must align with criticality_score).
        - criticality_score (integer 1-10): based on business impact, blast radius, and dependency count.
        - hide_by_default: true only when the node is low-signal or noisy at architecture level.
        - confidence: numeric 0-1 reflecting certainty of the annotation.
        - reason: brief justification (one sentence).

        2) Optionally suggest missing relationships between existing nodes only:
        - Prefer returning no suggested edges over low-confidence suggestions.
        - Only suggest an edge when confidence ≥ 0.5.
        - Use status "proposed" only; never assert authoritative edges.
        - Provide relationship label, reason, and confidence.

        Output format (JSON only):
        {
        "nodes": [
            {
            "node_id": "<existing node id>",
            "annotations": {
                "display_name": "...",
                "service_display_name": "...",
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

        Return JSON only with the keys shown. Do not include prose, markdown, or explanations.

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
    if not endpoint or not deployment:
        raise RuntimeError("Azure OpenAI endpoint or deployment not configured")

    # Acquire AAD token so we do not rely on API keys.
    credential = DefaultAzureCredential()
    token = credential.get_token("https://cognitiveservices.azure.com/.default")

    client = AzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token=token.token,
        api_version="2024-05-01-preview",
    )

    messages = [
        {"role": "system", "content": ARCHITECT_ANNOTATION_PROMPT},
        {
            "role": "user",
            "content": f"Graph summary (authoritative):\n{json.dumps(summary, ensure_ascii=False)}",
        },
    ]

    max_attempts = 2
    timeout_seconds = 15  # keep requests bounded for API responsiveness

    for attempt in range(1, max_attempts + 1):
        try:
            completion = client.chat.completions.create(
                model=deployment,
                messages=messages,
                temperature=0.1,  # low temperature to stay deterministic
                max_tokens=3000,  # increased to handle larger graphs without truncation
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
        except Exception:
            LOGGER.warning("Azure OpenAI attempt %s/%s failed", attempt, max_attempts)
            if attempt == max_attempts:
                raise
            sleep(1)  # brief backoff before retry
