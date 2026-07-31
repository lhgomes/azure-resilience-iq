"""
Chat service for LLM-based infrastructure analysis.
Provides intelligent analysis of Azure infrastructure, recommendations, and guidance.
"""

import json
import logging
import os
import re
from typing import List, Optional, Dict, Any, Tuple
from urllib.parse import urlsplit
from uuid import uuid4

from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizableTextQuery
import hcl2

from app.config import get_resources_path, get_node_overrides_path
from app.settings import get_settings
from app.chat.models import ChatResponse, SuggestedEdge, CriticalityInsight, ChatSource, ChatMetrics
from app.llm.gateway import create_llm_gateway
from app.storage.agent_memory_store import (
    append_cross_flow_handoff,
    build_graph_context_fingerprint,
    compact_resource_ids,
    load_cross_flow_handoffs,
)
from app.storage.conversation_store import (
    get_subscription_conversation_id,
    get_subscription_context_fingerprint,
    get_workload_conversation_id,
    get_workload_context_fingerprint,
    is_subscription_context_seeded,
    is_workload_context_seeded,
    set_subscription_conversation_id,
    set_subscription_context_fingerprint,
    set_subscription_context_seeded,
    set_workload_conversation_id,
    set_workload_context_fingerprint,
    set_workload_context_seeded,
)
from app.storage.workload_store import get_workload as get_saved_workload
from app.storage._json_repo import read_json, path_exists

LOGGER = logging.getLogger(__name__)


def _is_allowed_source_url(value: str) -> bool:
    candidate = (value or "").strip()
    if not candidate:
        return False

    parsed = urlsplit(candidate)
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    return bool(parsed.netloc)


class ChatService:
    """Service for handling chat interactions with LLM context."""

    def __init__(self):
        """Initialize chat service with direct Foundry gateway and scope classifier guardrails."""
        self.settings = get_settings()
        self.llm_config = self.settings.get_llm_config()
        self.llm_generation_config = self.settings.get_llm_generation_config()
        self.ai_agent_config = self.settings.get_ai_agent_config()
        
        LOGGER.debug(f"Chat service initialized with LLM config: {self.llm_config}")
        
        self.llm_gateway = create_llm_gateway(self.settings)
        self._terraform_search_client: Optional[SearchClient] = None
        self._resource_index_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._node_override_index_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        LOGGER.info("Using embedded-agent instructions with runtime context prompts")

    async def process_query(
        self,
        query: str,
        graph: Dict[str, Any],
        subscription_id: str,
        context: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        referenced_resource_ids: Optional[List[str]] = None,
        include_rag_trace: bool = False,
    ) -> ChatResponse:
        """
        Process user query with graph context using LLM.

        Args:
            query: User's natural language query
            graph: Current infrastructure graph
            subscription_id: Azure subscription ID
            context: Additional context (selected node, tab, etc.)
            conversation_history: Previous messages in conversation

        Returns:
            ChatResponse with message, suggestions, and insights
        """
        if not self.llm_gateway.is_available():
            return ChatResponse(
                message="LLM service is not available. Please check configuration."
            )

        LOGGER.debug(f"Processing chat query: {query[:100]}...")
        trace_id = str(uuid4())
        flow = "chat"

        try:
            preferred_flow = self._extract_preferred_flow(context)
            if preferred_flow:
                flow, target_agent_id = self._resolve_agent_for_flow(preferred_flow)
                query_type = "terraform" if flow == "terraform" else self._detect_query_type(query)
            else:
                query_type = self._detect_query_type(query)
                flow, target_agent_id = self._resolve_agent_for_query_type(query_type)
            if not target_agent_id:
                flow_env_hint = {
                    "terraform": "AI_FOUNDRY_TERRAFORM_AGENT_REFERENCE",
                    "chat": "AI_FOUNDRY_CHAT_AGENT_REFERENCE",
                }.get(flow, "AI_FOUNDRY_CHAT_AGENT_REFERENCE")
                return ChatResponse(
                    message=(
                        f"Agent configuration missing for {flow} flow. "
                        f"Set {flow_env_hint}."
                    )
                )

            normalized_graph = self._normalize_graph(graph)
            scope = self._resolve_conversation_scope(subscription_id, context, normalized_graph)
            context_fingerprint = build_graph_context_fingerprint(normalized_graph)
            conversation_id = self._load_conversation_id(scope["scope_type"], scope["scope_id"], flow)
            if not conversation_id and scope["scope_type"] == "workload":
                conversation_id = get_subscription_conversation_id(scope["anchor_subscription_id"], flow)
            context_seeded = self._is_conversation_context_seeded(scope["scope_type"], scope["scope_id"], flow)
            stored_context_fingerprint = self._load_conversation_context_fingerprint(
                scope["scope_type"],
                scope["scope_id"],
                flow,
            )
            if conversation_id and stored_context_fingerprint != context_fingerprint:
                LOGGER.info(
                    "Rotating stale conversation for scope=%s:%s flow=%s stored_fingerprint=%s current_fingerprint=%s",
                    scope["scope_type"],
                    scope["scope_id"],
                    flow,
                    stored_context_fingerprint or "legacy",
                    context_fingerprint,
                )
                self._clear_conversation_id(scope["scope_type"], scope["scope_id"], flow)
                self._set_conversation_context_seeded(scope["scope_type"], scope["scope_id"], flow, False)
                conversation_id = None
                context_seeded = False
            cross_flow_handoffs = load_cross_flow_handoffs(
                scope["scope_type"],
                scope["scope_id"],
                flow,
                context_fingerprint,
            )
            LOGGER.debug(
                "Chat conversation scope=%s id=%s anchor_subscription=%s conversation_id=%s context_seeded=%s",
                scope["scope_type"],
                scope["scope_id"],
                scope["anchor_subscription_id"],
                conversation_id,
                context_seeded,
            )
            is_valid, reason = self._classify_query_scope(
                query,
                agent_id=target_agent_id,
                conversation_id=conversation_id,
            )
            if not is_valid:
                LOGGER.info("Query rejected by local scope guardrails: %s", query[:100])
                return ChatResponse(message=reason)

            # Build prompt with context
            effective_referenced_resource_ids = self._collect_referenced_resource_ids(
                query=query,
                context=context,
                referenced_resource_ids=referenced_resource_ids,
            )
            llm_output: Dict[str, Any]
            ran_preflight_only = False
            llm_call_phases: List[str] = []

            if flow == "terraform" and self._should_run_terraform_preflight(
                query=query,
                context=context,
                conversation_id=conversation_id,
                context_seeded=context_seeded,
            ):
                preflight_prompt = self._build_terraform_preflight_prompt(
                    query=query,
                    graph=normalized_graph,
                    subscription_id=subscription_id,
                    context=context,
                    referenced_resource_ids=effective_referenced_resource_ids,
                )
                preflight_output, preflight_conversation_recovered = self._generate_json_with_conversation_recovery(
                    query=query,
                    normalized_graph=normalized_graph,
                    subscription_id=subscription_id,
                    context=context,
                    query_type=query_type,
                    flow=flow,
                    prompt=preflight_prompt,
                    referenced_resource_ids=effective_referenced_resource_ids,
                    target_agent_id=target_agent_id,
                    scope_type=scope["scope_type"],
                    scope_id=scope["scope_id"],
                    conversation_id=conversation_id,
                    cross_flow_handoffs=cross_flow_handoffs,
                )
                llm_call_phases.append("terraform_preflight")

                if preflight_conversation_recovered:
                    conversation_id = None
                    context_seeded = False

                preflight_questions = self._normalize_terraform_clarifying_questions(
                    preflight_output.get("clarifying_questions", [])
                )
                if preflight_questions:
                    preflight_output["clarifying_questions"] = preflight_questions
                    preflight_output["files"] = []
                    preflight_output.pop("terraform_code", None)
                    preflight_output.pop("terraform", None)
                    preflight_output.pop("code", None)
                    llm_output = preflight_output
                    ran_preflight_only = True

            if not ran_preflight_only:
                terraform_catalog_evidence: List[Dict[str, str]] = []
                if flow == "terraform":
                    terraform_catalog_evidence = self._retrieve_terraform_catalog_evidence(
                        query=query,
                        graph=normalized_graph,
                    )
                prompt = self._build_prompt(
                    query,
                    normalized_graph,
                    subscription_id,
                    context,
                    query_type,
                    effective_referenced_resource_ids,
                    include_full_context=not context_seeded,
                    cross_flow_handoffs=cross_flow_handoffs,
                )
                if flow == "terraform":
                    prompt = self._append_terraform_catalog_evidence(
                        prompt,
                        terraform_catalog_evidence,
                    )

                llm_output, conversation_recovered = self._generate_json_with_conversation_recovery(
                    query=query,
                    normalized_graph=normalized_graph,
                    subscription_id=subscription_id,
                    context=context,
                    query_type=query_type,
                    flow=flow,
                    prompt=prompt,
                    referenced_resource_ids=effective_referenced_resource_ids,
                    target_agent_id=target_agent_id,
                    scope_type=scope["scope_type"],
                    scope_id=scope["scope_id"],
                    conversation_id=conversation_id,
                    cross_flow_handoffs=cross_flow_handoffs,
                )
                llm_call_phases.append("main_generation")

                if conversation_recovered:
                    conversation_id = None
                    context_seeded = False

                if flow == "terraform":
                    llm_output["_backend_retrieval_evidence"] = terraform_catalog_evidence

            llm_output, unresolved_references = self._canonicalize_llm_references(
                llm_output,
                normalized_graph,
            )
            if unresolved_references:
                LOGGER.warning(
                    "Removed %d unresolved or ambiguous LLM graph references after local normalization.",
                    len(unresolved_references),
                )

            llm_metrics = self.llm_gateway.get_last_metrics()
            generated_conversation_id = None
            if isinstance(llm_metrics, dict):
                generated_conversation_id = llm_metrics.get("conversation_id")
            if generated_conversation_id and str(generated_conversation_id).strip():
                self._persist_conversation_id(
                    scope_type=scope["scope_type"],
                    scope_id=scope["scope_id"],
                    flow=flow,
                    conversation_id=str(generated_conversation_id).strip(),
                )
                self._persist_conversation_context_fingerprint(
                    scope_type=scope["scope_type"],
                    scope_id=scope["scope_id"],
                    flow=flow,
                    context_fingerprint=context_fingerprint,
                )
                if scope["scope_type"] == "workload":
                    set_subscription_conversation_id(
                        scope["anchor_subscription_id"],
                        flow,
                        str(generated_conversation_id).strip(),
                    )
                    set_subscription_context_fingerprint(
                        scope["anchor_subscription_id"],
                        flow,
                        context_fingerprint,
                    )
            if not context_seeded:
                self._mark_conversation_context_seeded(
                    scope_type=scope["scope_type"],
                    scope_id=scope["scope_id"],
                    flow=flow,
                )

            LOGGER.info(
                "Chat request trace_id=%s flow=%s query_type=%s phases=%s conversation_present=%s context_seeded=%s retries=%s total_tokens=%s",
                trace_id,
                flow,
                query_type,
                ",".join(llm_call_phases) if llm_call_phases else "none",
                bool(conversation_id and str(conversation_id).strip()),
                context_seeded,
                (llm_metrics or {}).get("rate_limit_retries") if isinstance(llm_metrics, dict) else None,
                (llm_metrics or {}).get("total_tokens") if isinstance(llm_metrics, dict) else None,
            )

            # Validate and enrich response
            clarification_limit_reached = (
                flow == "terraform"
                and self._is_terraform_finalization_query(query)
                and bool(self._normalize_clarifying_questions(llm_output.get("clarifying_questions", [])))
            )
            response = self._process_llm_response(
                llm_output,
                normalized_graph,
                llm_metrics,
                query=query,
                query_type=query_type,
                flow=flow,
                include_rag_trace=include_rag_trace,
                target_agent_id=target_agent_id,
                scope_type=scope["scope_type"],
                scope_id=scope["scope_id"],
                conversation_id=generated_conversation_id or conversation_id,
                trace_id=trace_id,
            )
            if clarification_limit_reached:
                self._clear_conversation_state(
                    scope_type=scope["scope_type"],
                    scope_id=scope["scope_id"],
                    anchor_subscription_id=scope["anchor_subscription_id"],
                    flow=flow,
                )
            self._append_cross_flow_handoff(
                scope_type=scope["scope_type"],
                scope_id=scope["scope_id"],
                flow=flow,
                context_fingerprint=context_fingerprint,
                query=query,
                response=response,
                graph=normalized_graph,
            )
            return response

        except json.JSONDecodeError as e:
            LOGGER.error(f"Failed to parse LLM JSON response: {e}")
            return ChatResponse(
                message="I had trouble understanding the response. Please try again.",
                rag_trace={
                    "trace_id": trace_id,
                    "flow": flow,
                    "error": "invalid_json",
                    "rag_expected": flow == "resilience",
                } if include_rag_trace else None,
            )
        except Exception as e:
            if self._is_rate_limit_error(e):
                LOGGER.warning("Chat service throttled by upstream LLM provider: %s", e)
                return ChatResponse(
                    message=(
                        "The AI service is temporarily busy (rate limited). "
                        "Please retry in a few seconds."
                    ),
                    rag_trace={
                        "trace_id": trace_id,
                        "flow": flow,
                        "error": "rate_limited",
                        "rag_expected": flow == "resilience",
                    } if include_rag_trace else None,
                )

            LOGGER.error(f"Chat service error: {e}", exc_info=True)
            return ChatResponse(
                message=f"Error processing query: {str(e)}",
                rag_trace={
                    "trace_id": trace_id,
                    "flow": flow,
                    "error": str(e),
                    "rag_expected": flow == "resilience",
                } if include_rag_trace else None,
            )

    @staticmethod
    def _is_conversation_not_found_error(error: Exception) -> bool:
        text = str(error).lower()
        return "conversation_not_found" in text or (
            "conversation" in text and "not found" in text
        )

    @staticmethod
    def _is_rate_limit_error(error: Exception) -> bool:
        text = str(error).lower()
        if "rate limit" in text or "too many requests" in text or "too_many_requests" in text:
            return True

        status_code = getattr(error, "status_code", None)
        if status_code == 429:
            return True

        response = getattr(error, "response", None)
        response_status = getattr(response, "status_code", None)
        if response_status == 429:
            return True

        return " 429" in text or "(429)" in text

    @staticmethod
    def _clear_conversation_id(scope_type: str, scope_id: str, flow: str) -> None:
        if scope_type == "workload":
            set_workload_conversation_id(scope_id, flow, "")
            return
        set_subscription_conversation_id(scope_id, flow, "")

    @staticmethod
    def _set_conversation_context_seeded(scope_type: str, scope_id: str, flow: str, seeded: bool) -> None:
        if scope_type == "workload":
            set_workload_context_seeded(scope_id, flow, seeded)
            return
        set_subscription_context_seeded(scope_id, flow, seeded)

    @staticmethod
    def _clear_conversation_context_fingerprint(scope_type: str, scope_id: str, flow: str) -> None:
        if scope_type == "workload":
            set_workload_context_fingerprint(scope_id, flow, "")
            return
        set_subscription_context_fingerprint(scope_id, flow, "")

    def _clear_conversation_state(
        self,
        *,
        scope_type: str,
        scope_id: str,
        anchor_subscription_id: str,
        flow: str,
    ) -> None:
        self._clear_conversation_id(scope_type, scope_id, flow)
        self._set_conversation_context_seeded(scope_type, scope_id, flow, False)
        self._clear_conversation_context_fingerprint(scope_type, scope_id, flow)
        if scope_type == "workload":
            self._clear_conversation_id("subscription", anchor_subscription_id, flow)
            self._set_conversation_context_seeded("subscription", anchor_subscription_id, flow, False)
            self._clear_conversation_context_fingerprint("subscription", anchor_subscription_id, flow)

    def _generate_json_with_conversation_recovery(
        self,
        *,
        query: str,
        normalized_graph: Dict[str, Any],
        subscription_id: str,
        context: Optional[Dict[str, Any]],
        query_type: str,
        flow: str,
        prompt: str,
        referenced_resource_ids: Optional[List[str]],
        target_agent_id: Optional[str],
        scope_type: str,
        scope_id: str,
        conversation_id: Optional[str],
        cross_flow_handoffs: List[Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], bool]:
        try:
            return (
                self.llm_gateway.generate_json(
                    system_prompt=self._system_prompt(query_type, flow=flow),
                    user_prompt=prompt,
                    temperature=0.7,
                    max_tokens=self.llm_generation_config.get('max_tokens', 2000),
                    model=self.llm_generation_config.get('model'),
                    agent_id=target_agent_id,
                    conversation_id=conversation_id,
                ),
                False,
            )
        except Exception as error:
            if not conversation_id or not self._is_conversation_not_found_error(error):
                raise

            LOGGER.warning(
                "Conversation id '%s' is no longer valid for scope=%s:%s. "
                "Creating a new conversation, replaying full workload context, and retrying once.",
                conversation_id,
                scope_type,
                scope_id,
            )

            self._clear_conversation_id(scope_type, scope_id, flow)
            self._set_conversation_context_seeded(scope_type, scope_id, flow, False)

            retry_prompt = self._build_prompt(
                query,
                normalized_graph,
                subscription_id,
                context,
                query_type,
                referenced_resource_ids,
                include_full_context=True,
                cross_flow_handoffs=cross_flow_handoffs,
            )

            return (
                self.llm_gateway.generate_json(
                    system_prompt=self._system_prompt(query_type, flow=flow),
                    user_prompt=retry_prompt,
                    temperature=0.7,
                    max_tokens=self.llm_generation_config.get('max_tokens', 2000),
                    model=self.llm_generation_config.get('model'),
                    agent_id=target_agent_id,
                    conversation_id=None,
                ),
                True,
            )

    @staticmethod
    def _extract_selected_subscriptions(context: Optional[Dict[str, Any]]) -> List[str]:
        if not isinstance(context, dict):
            return []
        selected = context.get("selected_subscriptions")
        if not isinstance(selected, list):
            return []
        values = [str(item).strip() for item in selected if str(item).strip()]
        # keep deterministic order with first occurrence
        seen: set[str] = set()
        ordered: List[str] = []
        for sub_id in values:
            if sub_id in seen:
                continue
            seen.add(sub_id)
            ordered.append(sub_id)
        return ordered

    @staticmethod
    def _count_nodes_by_subscription(graph: Dict[str, Any]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for node in graph.get("nodes", []) or []:
            if not isinstance(node, dict):
                continue
            node_sub = (
                node.get("subscription_id")
                or (node.get("metadata") or {}).get("subscription_id")
                or (node.get("data") or {}).get("subscription_id")
            )
            if not node_sub:
                continue
            sub_id = str(node_sub).strip()
            if not sub_id:
                continue
            counts[sub_id] = counts.get(sub_id, 0) + 1
        return counts

    def _resolve_conversation_scope(
        self,
        subscription_id: str,
        context: Optional[Dict[str, Any]],
        graph: Dict[str, Any],
    ) -> Dict[str, str]:
        selected_subscriptions = self._extract_selected_subscriptions(context)
        if not selected_subscriptions:
            selected_subscriptions = [subscription_id]

        workload_id = None
        if isinstance(context, dict):
            workload_id = (
                context.get('workload_id')
                or context.get('selected_workload_id')
                or context.get('active_workload_id')
            )
        if workload_id:
            clean_workload_id = str(workload_id).strip()
            if clean_workload_id and get_saved_workload(clean_workload_id):
                anchor_subscription = selected_subscriptions[0]
                return {
                    "scope_type": "workload",
                    "scope_id": clean_workload_id,
                    "anchor_subscription_id": anchor_subscription,
                }

        if len(selected_subscriptions) >= 2:
            counts = self._count_nodes_by_subscription(graph)
            ranked = sorted(
                selected_subscriptions,
                key=lambda sub_id: (-counts.get(sub_id, 0), sub_id),
            )
            anchor_subscription = ranked[0]
            return {
                "scope_type": "subscription",
                "scope_id": anchor_subscription,
                "anchor_subscription_id": anchor_subscription,
            }

        anchor_subscription = selected_subscriptions[0]
        return {
            "scope_type": "subscription",
            "scope_id": anchor_subscription,
            "anchor_subscription_id": anchor_subscription,
        }

    @staticmethod
    def _load_conversation_id(scope_type: str, scope_id: str, flow: str) -> Optional[str]:
        if scope_type == "workload":
            return get_workload_conversation_id(scope_id, flow)
        return get_subscription_conversation_id(scope_id, flow)

    @staticmethod
    def _persist_conversation_id(scope_type: str, scope_id: str, flow: str, conversation_id: str) -> None:
        if scope_type == "workload":
            set_workload_conversation_id(scope_id, flow, conversation_id)
            return
        set_subscription_conversation_id(scope_id, flow, conversation_id)

    @staticmethod
    def _load_conversation_context_fingerprint(scope_type: str, scope_id: str, flow: str) -> Optional[str]:
        if scope_type == "workload":
            return get_workload_context_fingerprint(scope_id, flow)
        return get_subscription_context_fingerprint(scope_id, flow)

    @staticmethod
    def _persist_conversation_context_fingerprint(
        scope_type: str,
        scope_id: str,
        flow: str,
        context_fingerprint: str,
    ) -> None:
        if scope_type == "workload":
            set_workload_context_fingerprint(scope_id, flow, context_fingerprint)
            return
        set_subscription_context_fingerprint(scope_id, flow, context_fingerprint)

    @staticmethod
    def _is_conversation_context_seeded(scope_type: str, scope_id: str, flow: str) -> bool:
        if scope_type == "workload":
            return is_workload_context_seeded(scope_id, flow)
        return is_subscription_context_seeded(scope_id, flow)

    @staticmethod
    def _mark_conversation_context_seeded(scope_type: str, scope_id: str, flow: str) -> None:
        if scope_type == "workload":
            set_workload_context_seeded(scope_id, flow, True)
            return
        set_subscription_context_seeded(scope_id, flow, True)

    def _resolve_agent_for_query_type(self, query_type: str) -> Tuple[str, Optional[str]]:
        """Resolve flow and agent reference with safe fallback behavior."""
        preferred_flow = "terraform" if query_type == "terraform" else "chat"
        return self._resolve_agent_for_flow(preferred_flow)

    def _get_terraform_search_client(self) -> Optional[SearchClient]:
        if self._terraform_search_client is not None:
            return self._terraform_search_client

        endpoint = str(os.getenv("AZURE_SEARCH_ENDPOINT") or "").strip().rstrip("/")
        index_name = str(
            os.getenv("AZURE_SEARCH_INDEX_NAME_TERRAFORM")
            or self.ai_agent_config.get("index_name_terraform")
            or ""
        ).strip()
        if not endpoint or not index_name:
            LOGGER.error(
                "Terraform catalog retrieval is unavailable: AZURE_SEARCH_ENDPOINT and "
                "AZURE_SEARCH_INDEX_NAME_TERRAFORM are required."
            )
            return None

        self._terraform_search_client = SearchClient(
            endpoint=endpoint,
            index_name=index_name,
            credential=DefaultAzureCredential(),
        )
        return self._terraform_search_client

    @staticmethod
    def _failed_finding_descriptions(graph: Dict[str, Any]) -> List[str]:
        evaluations = (graph.get("resilience_evaluations") or {}).get("evaluations", {})
        return [
            str(check.get("description") or "").strip()
            for payload in evaluations.values()
            for check in (payload.get("checks", []) if isinstance(payload, dict) else [])
            if isinstance(check, dict)
            and check.get("status") == "fail"
            and str(check.get("description") or "").strip()
        ]

    def _retrieve_terraform_catalog_evidence(
        self,
        *,
        query: str,
        graph: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        client = self._get_terraform_search_client()
        if client is None:
            return []

        findings = self._failed_finding_descriptions(graph)
        search_text = "\n".join([query.strip(), *findings[:12]]).strip()
        if not search_text:
            return []

        try:
            results = client.search(
                search_text=search_text,
                vector_queries=[
                    VectorizableTextQuery(
                        text=search_text,
                        k_nearest_neighbors=5,
                        fields="content_vector",
                    )
                ],
                query_type="semantic",
                semantic_configuration_name="rag-semantic-config",
                select=["id", "title", "content", "source", "source_type"],
                top=5,
            )
            evidence: List[Dict[str, str]] = []
            index_name = str(os.getenv("AZURE_SEARCH_INDEX_NAME_TERRAFORM") or "terraform")
            for result in results:
                document_id = str(result.get("id") or "").strip()
                content = str(result.get("content") or "").strip()
                if not document_id or not content:
                    continue
                evidence.append(
                    {
                        "ref": f"search:{index_name}:{document_id}",
                        "title": str(result.get("title") or "").strip()[:240],
                        "source": str(result.get("source") or result.get("source_type") or "").strip()[:80],
                        "content": content[:1600],
                    }
                )
            return evidence
        except Exception as error:  # noqa: BLE001
            LOGGER.error("Terraform backend hybrid catalog retrieval failed: %s", error)
            return []

    @staticmethod
    def _append_terraform_catalog_evidence(
        prompt: str,
        evidence: List[Dict[str, str]],
    ) -> str:
        if not evidence:
            return (
                f"{prompt}\n\nBACKEND MODULE CATALOG STATUS: no matching AVM/CAF evidence.\n"
                "Do not claim or emit AVM/CAF modules. Use native azurerm resources and set source_refs: [] "
                "for native_azurerm decisions. Generated native HCL must satisfy every failed control and will be "
                "validated by the backend. If existing resources are not Terraform-managed or current HCL is unavailable, "
                "generate a side-by-side net-new resilient architecture instead of blocking."
            )

        rendered = json.dumps(evidence, ensure_ascii=True, separators=(",", ":"))
        return (
            f"{prompt}\n\nBACKEND-VERIFIED TERRAFORM CATALOG EVIDENCE:\n{rendered}\n\n"
            "Use these records for AVM/CAF module claims. Copy each selected module record's ref into "
            "module_decisions[*].source_refs. When no compatible module is evidenced, use native azurerm resources "
            "with source_refs: []; native HCL will be validated by the backend."
        )

    def _resolve_agent_for_flow(self, preferred_flow: str) -> Tuple[str, Optional[str]]:
        """Resolve flow and agent reference with safe fallback behavior."""
        preferred_agent = self.settings.get_agent_id_for_flow(preferred_flow)
        if preferred_agent:
            return preferred_flow, preferred_agent

        if preferred_flow != "chat":
            fallback_agent = self.settings.get_agent_id_for_flow("chat")
            if fallback_agent:
                LOGGER.info(
                    "No dedicated %s agent configured; falling back to chat agent",
                    preferred_flow,
                )
                return "chat", fallback_agent

        return preferred_flow, None

    @staticmethod
    def _extract_preferred_flow(context: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(context, dict):
            return None

        candidates = [
            context.get("preferred_agent_flow"),
            context.get("agent_flow"),
            context.get("source_agent_flow"),
        ]
        for candidate in candidates:
            value = str(candidate or "").strip().lower()
            if value in {"chat", "terraform"}:
                return value
        return None

    def _detect_query_type(self, query: str) -> str:
        """Detect the type of query to route appropriately."""
        query_lower = query.lower()

        if self._is_capability_query(query):
            return 'capabilities'
        elif any(word in query_lower for word in ['failing', 'recommendation', 'check', 'issue', 'wrong']):
            return 'findings'
        elif any(word in query_lower for word in ['fix', 'remediat', 'resolv', 'how', 'steps']):
            return 'remediation'
        elif any(word in query_lower for word in ['terraform', 'iac', 'code', 'script']):
            return 'terraform'
        elif any(word in query_lower for word in ['connect', 'relationship', 'depend']):
            return 'connections'
        else:
            return 'general'

    @staticmethod
    def _is_capability_query(query: str) -> bool:
        """Return True for questions about the assistant's supported capabilities."""
        normalized = re.sub(r"[^a-z0-9\s]", "", (query or "").strip().lower())
        capability_queries = {
            "what can you help with",
            "what can you do",
            "how can you help",
            "how can you help me",
            "what do you do",
            "what are your capabilities",
            "show me your capabilities",
        }
        return normalized in capability_queries

    def _classify_query_scope(
        self,
        query: str,
        agent_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Classify whether a query is in-scope for workload infrastructure analysis.

        This uses lightweight local guardrails only to avoid a preflight LLM call.
        """
        query_lower = (query or "").strip().lower()
        if not query_lower:
            return False, "Please enter a question about your workload resources, risks, or remediation."

        off_topic_keywords = [
            "certification", "career", "interview", "salary", "movie", "joke", "recipe",
            "football", "weather today", "politics", "stock tips", "dating",
        ]
        if any(keyword in query_lower for keyword in off_topic_keywords):
            return False, "Please ask about your Azure workload resources, dependencies, findings, or remediation."

        return True, ""

    @staticmethod
    def _is_force_proceed_query(query: str) -> bool:
        """Return True when user explicitly asks to proceed despite missing clarifications."""
        query_lower = (query or "").strip().lower()
        if not query_lower:
            return False

        force_markers = [
            "proceed anyway",
            "continue anyway",
            "ignore questions",
            "ignore the questions",
            "use defaults",
            "assume defaults",
            "just generate",
            "generate anyway",
            "do not ask",
            "don't ask",
            "skip questions",
            "force",
        ]
        return any(marker in query_lower for marker in force_markers)

    @staticmethod
    def _is_clarification_answer_query(query: str) -> bool:
        return (query or "").strip().casefold().startswith("clarification answers:")

    def _is_terraform_finalization_query(self, query: str) -> bool:
        return self._is_clarification_answer_query(query) or self._is_force_proceed_query(query)

    def _should_run_terraform_preflight(
        self,
        *,
        query: str,
        context: Optional[Dict[str, Any]],
        conversation_id: Optional[str],
        context_seeded: bool,
    ) -> bool:
        """Run preflight only when needed to avoid repeated token-heavy checks on follow-ups."""
        if self._is_force_proceed_query(query):
            return False

        if isinstance(context, dict):
            if bool(context.get("force_terraform_preflight")):
                return True
            if bool(context.get("skip_terraform_preflight")):
                return False

        has_conversation = bool(conversation_id and str(conversation_id).strip())
        if has_conversation:
            return False

        return True

    @staticmethod
    def _normalize_clarifying_questions(raw_value: Any) -> List[Dict[str, Any]]:
        raw_list = raw_value if isinstance(raw_value, list) else [raw_value]
        normalized: List[Dict[str, Any]] = []

        for item in raw_list:
            if isinstance(item, dict):
                question = str(item.get("question") or "").strip()
                answers_raw = item.get("possible_answers", [])
                answers_list = answers_raw if isinstance(answers_raw, list) else [answers_raw]
                possible_answers = [str(answer).strip() for answer in answers_list if str(answer).strip()]
                if question:
                    normalized.append({
                        "question": question,
                        "possible_answers": possible_answers,
                    })
                continue

            question_text = str(item).strip()
            if question_text:
                normalized.append({
                    "question": question_text,
                    "possible_answers": [],
                })

        return normalized

    @staticmethod
    def _is_agent_owned_platform_question(question: str) -> bool:
        normalized = " ".join((question or "").casefold().split())
        current_state_markers = (
            "current hcl",
            "existing hcl",
            "current module",
            "existing module",
            "currently deployed",
            "current sku",
            "existing sku",
            "terraform state",
        )
        if any(marker in normalized for marker in current_state_markers):
            return False

        decision_markers = (
            "should",
            "do you want",
            "which",
            "what exact",
            "proceed with",
            "choose",
            "select",
            "approve",
        )
        platform_markers = (
            "sku",
            "zrs",
            "gzrs",
            "redundan",
            "availability zone",
            "capability",
            "supported in",
            "best practice",
        )
        return (
            any(marker in normalized for marker in decision_markers)
            and any(marker in normalized for marker in platform_markers)
        )

    def _normalize_terraform_clarifying_questions(self, raw_value: Any) -> List[Dict[str, Any]]:
        questions = self._normalize_clarifying_questions(raw_value)
        filtered = [
            item
            for item in questions
            if not self._is_agent_owned_platform_question(str(item.get("question") or ""))
        ]
        removed_count = len(questions) - len(filtered)
        if removed_count:
            LOGGER.warning(
                "Removed %d Terraform clarification question(s) that delegated Azure platform decisions to the user.",
                removed_count,
            )
        return filtered

    @staticmethod
    def _extract_terraform_compiler_blocker(llm_output: Dict[str, Any]) -> Optional[str]:
        rag_trace = llm_output.get("rag_trace")
        if not isinstance(rag_trace, dict):
            return None

        notes_raw = rag_trace.get("notes")
        notes = notes_raw if isinstance(notes_raw, list) else []
        bounded_notes = [
            " ".join(str(note).split())[:600]
            for note in notes[:5]
            if str(note).strip()
        ]
        if not bounded_notes:
            return None

        LOGGER.warning("Terraform compiler blocker details: %s", " | ".join(bounded_notes))
        return (
            "The Terraform compiler returned no configuration files after processing the supplied constraints. "
            "No infrastructure changes were generated."
        )

    def _build_terraform_preflight_prompt(
        self,
        *,
        query: str,
        graph: Dict[str, Any],
        subscription_id: str,
        context: Optional[Dict[str, Any]],
        referenced_resource_ids: Optional[List[str]],
    ) -> str:
        failed_findings_summary = self._summarize_failed_findings(
            graph,
            max_resources=12,
            max_checks_per_resource=2,
            include_long_description=False,
            include_benefits=False,
            include_links=False,
        )
        detailed_resource_context = self._build_detailed_resource_context(
            query=query,
            graph=graph,
            subscription_id=subscription_id,
            context=context,
            force=True,
        )
        referenced_resource_context = self._build_referenced_resource_context(
            referenced_resource_ids=referenced_resource_ids,
            subscription_id=subscription_id,
        )

        return f"""
PREFLIGHT MODE (Terraform clarification gate):
- Determine whether additional clarifications are required before safe Terraform generation.
- If clarifications are required, return clarifying_questions and do not generate Terraform files.
- If no clarifications are required, return clarifying_questions as an empty list.
- Ask only for missing deployment intent, current-state facts, constraints, or Terraform state ownership.
- Never ask the user for AVM/CAF module names or versions. Module selection is the compiler's responsibility.
- Never ask the user to select or approve an Azure SKU, redundancy mode, availability-zone design, or regional capability check.
- Azure best-practice selection and capability verification are compiler responsibilities. Prefer the strongest compatible option that satisfies the failed resilience control.
- For Azure Storage maximum durability, prefer GZRS; use RA-GZRS only where secondary read access is supported and useful; use ZRS when geo-zone redundancy is incompatible with the account kind, workload, or region.
- If Terraform ownership or current HCL is not provided, default to a side-by-side net-new architecture using the same Azure service families. Add only complementary services required to satisfy failed resilience controls.
- Do not ask for destructive-change permission when a non-destructive net-new replacement can be generated.
- Ask about business constraints such as permitted regions, RTO/RPO, data residency, or destructive-change tolerance only when they are not already provided.
- During generation, retrieve matching module evidence; if none is available, fall back to verified native azurerm resources.
- Possible answers must be complete actionable choices, never placeholders such as "with explicit version".

Authoritative failed findings:
{failed_findings_summary}

Detailed resource facts (when available):
{detailed_resource_context}

Explicitly referenced resources (# mentions):
{referenced_resource_context}

USER QUERY: {query}
"""

    def _sanitize_response_message(self, message: str) -> str:
        """
        Sanitize response message to remove any off-topic content.

        Checks for patterns that might indicate the LLM is answering off-topic questions.
        """
        max_length = 10000
        if len(message) > max_length:
            LOGGER.warning(f"Response message unusually long ({len(message)} chars), truncating")
            message = message[:max_length] + "\n\n(Message truncated due to length)"

        # Remove recommendation_id tags from user-facing prose (IDs remain in structured fields)
        message = re.sub(r"\s*\[recommendation_id:\s*[^\]]+\]", "", message, flags=re.IGNORECASE)

        # Improve readability of compact enumerations: "1) ... 2) ..." -> line-separated list
        message = re.sub(r"\s*(\d+\))\s*", r"\n\1 ", message)

        # Normalize whitespace/newlines after transformations
        message = re.sub(r"\n{3,}", "\n\n", message)
        message = message.strip()

        return message

    def _normalize_graph(self, graph: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize graph by converting Pydantic models to dictionaries.

        The graph from get_workload_graph() may contain Edge and Node objects
        that need to be serialized for use in prompts.
        """
        normalized = dict(graph)

        if 'nodes' in normalized:
            nodes = normalized['nodes']
            normalized['nodes'] = [
                n.model_dump() if hasattr(n, 'model_dump') else n
                for n in nodes
            ]

        if 'edges' in normalized:
            edges = normalized['edges']
            normalized['edges'] = [
                e.model_dump() if hasattr(e, 'model_dump') else e
                for e in edges
            ]

        return normalized

    @staticmethod
    def _first_non_empty(*values: Any) -> Any:
        for value in values:
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
        return None

    @staticmethod
    def _node_metadata(node: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(node, dict):
            return {}
        metadata = node.get('metadata')
        return metadata if isinstance(metadata, dict) else {}

    @staticmethod
    def _node_data(node: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(node, dict):
            return {}
        data = node.get('data')
        return data if isinstance(data, dict) else {}

    def _node_display_name(self, node: Dict[str, Any]) -> str:
        metadata = self._node_metadata(node)
        data = self._node_data(node)
        return str(
            self._first_non_empty(
                metadata.get('display_name'),
                data.get('label'),
                node.get('name'),
                node.get('id'),
                'Unknown',
            )
        )

    def _node_resource_type(self, node: Dict[str, Any]) -> str:
        metadata = self._node_metadata(node)
        data = self._node_data(node)
        return str(
            self._first_non_empty(
                metadata.get('azure_type'),
                data.get('resourceType'),
                node.get('type'),
                'Unknown',
            )
        )

    def _node_criticality(self, node: Dict[str, Any]) -> Any:
        metadata = self._node_metadata(node)
        data = self._node_data(node)
        return self._first_non_empty(
            metadata.get('criticality_score'),
            data.get('criticality_score'),
        )

    def _node_region(self, node: Dict[str, Any]) -> Optional[str]:
        metadata = self._node_metadata(node)
        data = self._node_data(node)
        value = self._first_non_empty(
            metadata.get('location'),
            metadata.get('region'),
            data.get('location'),
            data.get('region'),
        )
        return str(value) if value is not None else None

    def _node_zones(self, node: Dict[str, Any]) -> List[str]:
        metadata = self._node_metadata(node)
        data = self._node_data(node)
        raw = self._first_non_empty(
            metadata.get('zones'),
            data.get('zones'),
            metadata.get('zone'),
            metadata.get('availability_zone'),
        )
        if raw is None:
            return []
        if isinstance(raw, (list, tuple, set)):
            return [str(z).strip() for z in raw if str(z).strip()]
        text = str(raw).strip()
        return [text] if text else []

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _edge_relationship(self, edge: Dict[str, Any]) -> str:
        data = edge.get('data') if isinstance(edge.get('data'), dict) else {}
        metadata = edge.get('metadata') if isinstance(edge.get('metadata'), dict) else {}
        return str(
            self._first_non_empty(
                edge.get('relationship'),
                metadata.get('relationship'),
                data.get('relationship'),
                'relates_to',
            )
        )

    def _edge_confidence(self, edge: Dict[str, Any], default: float = 1.0) -> float:
        data = edge.get('data') if isinstance(edge.get('data'), dict) else {}
        metadata = edge.get('metadata') if isinstance(edge.get('metadata'), dict) else {}
        raw_value = self._first_non_empty(
            edge.get('confidence'),
            metadata.get('confidence'),
            data.get('confidence'),
        )
        return self._to_float(raw_value, default)

    def _system_prompt(self, query_type: str, *, flow: str = "chat") -> str:
        """Minimal runtime hint; policy/contract lives in embedded agent instructions."""
        query_hint = {
            'findings': "focus on failed findings and remediation priority",
            'remediation': "focus on concrete remediation steps",
            'terraform': "focus on Terraform output only if explicitly requested",
            'connections': "focus on topology/dependency relationships",
            'capabilities': "describe supported capabilities directly; do not ask clarifying questions or return workload findings",
            'general': "focus on direct answer to user intent",
        }.get(query_type, "focus on direct answer to user intent")

        base_hint = (
            "Embedded agent instructions are authoritative for role, routing, RAG policy, and output schema. "
            f"Runtime context: flow={flow}; query_focus={query_hint}. "
            "Use only provided workload context; do not invent resources or relationships."
        )

        if flow == "terraform":
            return base_hint

        return (
            f"{base_hint} "
            "Recommendation contract: when returning recommendations, every item MUST include a valid 'recommendation_id' "
            "from the authoritative findings context. Do not emit title-only or free-text recommendations."
        )

    def _build_prompt(
        self,
        query: str,
        graph: Dict[str, Any],
        subscription_id: str,
        context: Optional[Dict[str, Any]],
        query_type: str,
        referenced_resource_ids: Optional[List[str]],
        include_full_context: bool,
        cross_flow_handoffs: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Build prompt with optional full graph context for first-turn grounding only."""
        if query_type == 'capabilities':
            prompt = (
                "CAPABILITY DISCOVERY MODE:\n"
                "- Answer the user's question by briefly describing the supported workload graph annotation, "
                "resilience assessment, recommendation review, remediation guidance, and Terraform capabilities.\n"
                "- Do not analyze the current workload or return recommendations, resources, sources, or clarifying questions.\n"
                "- Set all output arrays to empty and remediation_guide and terraform_code to null.\n"
            )
        elif include_full_context:
            nodes_summary = self._summarize_nodes(graph.get('nodes', []))
            edges_summary = self._summarize_edges(graph.get('edges', []), graph.get('nodes', []))
            resilience_groups_summary = self._summarize_resilience_groups(graph)
            llm_baseline_summary = self._build_llm_baseline_summary(graph)
            if query_type == 'terraform':
                failed_findings_summary = self._summarize_failed_findings(
                    graph,
                    max_resources=20,
                    max_checks_per_resource=2,
                    include_long_description=False,
                    include_benefits=False,
                    include_links=False,
                )
                recommendation_section = ""
            else:
                failed_findings_summary = self._summarize_failed_findings(graph)
                recommendation_id_catalog = self._build_recommendation_id_catalog(graph)
                recommendation_section = (
                    "Authoritative recommendation IDs (use these IDs only in recommendations):\n"
                    f"{recommendation_id_catalog}\n\n"
                )
            allowed_node_ids = self._build_node_id_catalog(graph)
            detailed_resource_context = self._build_detailed_resource_context(
                query=query,
                graph=graph,
                subscription_id=subscription_id,
                context=context,
            )
            referenced_resource_context = self._build_referenced_resource_context(
                referenced_resource_ids=referenced_resource_ids,
                subscription_id=subscription_id,
            )

            prompt = f"""
WORKLOAD CONTEXT:
Resources={len(graph.get('nodes', []))}, Relationships={len(graph.get('edges', []))}

Top resources by criticality:
{nodes_summary}

Key relationships:
{edges_summary}

Resilience groups (HA/redundancy membership; do not recommend changes already satisfied here):
{resilience_groups_summary}

Failed findings (authoritative):
{failed_findings_summary}

{recommendation_section}

Prior LLM baseline:
{llm_baseline_summary}

Detailed resource facts (when required):
{detailed_resource_context}

Explicitly referenced resources (# mentions):
{referenced_resource_context}

Allowed node IDs (authoritative for references):
{allowed_node_ids}

"""
        else:
            prompt = (
                "CONVERSATION CONTEXT: Reuse prior conversation context for this scope. "
                "Do not request a full graph replay unless strictly necessary.\n"
            )

        is_clarification_answer = self._is_clarification_answer_query(query)
        if query_type == "terraform" and self._is_terraform_finalization_query(query):
            prompt += (
                "\nTERRAFORM FINALIZATION MODE:\n"
                "- This is the only answer turn after clarification. Do not return more clarifying questions.\n"
                "- Generate complete, validated Terraform when the supplied facts are sufficient.\n"
                "- Otherwise return no files and state exactly which concrete inputs are still missing.\n"
                "- Never guess existing resource arguments, Terraform state ownership, regions, SKUs, or topology.\n"
            )
        shared_memory_context = self._format_cross_flow_handoffs(
            [] if is_clarification_answer else (cross_flow_handoffs or [])
        )
        if shared_memory_context:
            prompt += (
                "\nCROSS-FLOW HANDOFF MEMORY:\n"
                "Treat this as compact prior decisions/context, not as authoritative resource state. "
                "Current graph and findings override it.\n"
                f"{shared_memory_context}\n"
            )
        
        # Add context if available
        if context:
            if context.get('selected_resource_id'):
                selected_resource_id = context['selected_resource_id']
                prompt += f"User is currently viewing resource: {selected_resource_id}\n"
                selected_node = next(
                    (n for n in graph.get('nodes', []) if n.get('id') == selected_resource_id),
                    None
                )
                if selected_node:
                    selected_name = self._node_display_name(selected_node)
                    selected_type = self._node_resource_type(selected_node)
                    selected_criticality = self._node_criticality(selected_node)
                    prompt += "Selected resource details:\n"
                    prompt += f"- name: {selected_name}\n"
                    prompt += f"- type: {selected_type}\n"
                    if selected_criticality is not None:
                        prompt += f"- criticality_score: {selected_criticality}\n"
            if context.get('selected_recommendation_id'):
                prompt += f"User is looking at recommendation: {context['selected_recommendation_id']}\n"
            if context.get('tab'):
                prompt += f"In UI tab: {context['tab']}\n"

        if referenced_resource_ids:
            referenced_resource_context = self._build_referenced_resource_context(
                referenced_resource_ids=referenced_resource_ids,
                subscription_id=subscription_id,
            )
            prompt += "Referenced resources (# mentions):\n"
            prompt += f"{referenced_resource_context}\n"

        prompt += f"\nUSER QUERY: {query}\n"

        return prompt

    @staticmethod
    def _format_cross_flow_handoffs(handoffs: List[Dict[str, Any]]) -> str:
        lines: List[str] = []
        for handoff in handoffs:
            flow = str(handoff.get("flow") or "unknown").strip()
            user_intent = str(handoff.get("user_intent") or "").strip()
            answer_summary = str(handoff.get("answer_summary") or "").strip()
            resource_ids = handoff.get("resource_ids") or []
            recommendation_ids = handoff.get("recommendation_ids") or []
            clarifying_questions = handoff.get("clarifying_questions") or []
            parts = [f"flow={flow}"]
            if user_intent:
                parts.append(f"intent={user_intent}")
            if answer_summary:
                parts.append(f"outcome={answer_summary}")
            if resource_ids:
                parts.append(f"resources={','.join(str(value) for value in resource_ids)}")
            if recommendation_ids:
                parts.append(f"recommendations={','.join(str(value) for value in recommendation_ids)}")
            if clarifying_questions:
                parts.append(f"open_questions={' | '.join(str(value) for value in clarifying_questions)}")
            lines.append("- " + "; ".join(parts))
        return "\n".join(lines)

    def _append_cross_flow_handoff(
        self,
        *,
        scope_type: str,
        scope_id: str,
        flow: str,
        context_fingerprint: str,
        query: str,
        response: ChatResponse,
        graph: Dict[str, Any],
    ) -> None:
        resource_ids = compact_resource_ids(
            graph.get("nodes", []),
            response.resources_to_highlight,
        )
        recommendation_ids = [
            str(item.get("recommendation_id") or "")
            for item in response.recommendations
            if isinstance(item, dict) and item.get("recommendation_id")
        ]
        clarifying_questions = [
            str(item.get("question") or "")
            for item in response.clarifying_questions
            if isinstance(item, dict) and item.get("question")
        ]
        try:
            append_cross_flow_handoff(
                scope_type=scope_type,
                scope_id=scope_id,
                flow=flow,
                context_fingerprint=context_fingerprint,
                user_intent=query,
                answer_summary=response.message,
                resource_ids=resource_ids,
                recommendation_ids=recommendation_ids,
                clarifying_questions=clarifying_questions,
            )
        except Exception as error:
            LOGGER.warning("Could not persist cross-flow handoff memory: %s", error)

    def _collect_referenced_resource_ids(
        self,
        query: str,
        context: Optional[Dict[str, Any]],
        referenced_resource_ids: Optional[List[str]],
    ) -> List[str]:
        """Collect resource IDs explicitly referenced by user via # mentions or context."""
        candidates: List[str] = []

        if isinstance(referenced_resource_ids, list):
            candidates.extend(str(item).strip() for item in referenced_resource_ids if str(item).strip())

        if isinstance(context, dict):
            context_refs = context.get("referenced_resource_ids")
            if isinstance(context_refs, list):
                candidates.extend(str(item).strip() for item in context_refs if str(item).strip())

        candidates.extend(self._extract_hash_referenced_resource_ids_from_query(query))

        deduped: List[str] = []
        seen: set[str] = set()
        for resource_id in candidates:
            normalized = resource_id.strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(resource_id.strip())

        return deduped

    @staticmethod
    def _extract_hash_referenced_resource_ids_from_query(query: str) -> List[str]:
        """Extract #/subscriptions/... references from user query text."""
        if not query:
            return []
        matches = re.findall(r"#(/subscriptions/[a-z0-9_\-./]+)", query, flags=re.IGNORECASE)
        return [f"/{match.lstrip('/')}" for match in matches if str(match).strip()]

    @staticmethod
    def _extract_subscription_id_from_resource_id(resource_id: str) -> Optional[str]:
        """Extract subscription ID from an Azure resource ID."""
        if not resource_id:
            return None
        match = re.match(r"^/subscriptions/([a-z0-9\-]+)/", resource_id.strip(), flags=re.IGNORECASE)
        if not match:
            return None
        return match.group(1)

    def _query_requires_resource_details(self, query: str) -> bool:
        """Return True when low-level resource facts are likely required."""
        query_lower = (query or "").lower()
        detail_indicators = [
            "disk size", "size gb", "sku", "vm sku", "vm size", "instance size",
            "backup", "retention", "retention policy", "restore", "recovery point",
            "cost", "pricing", "estimate", "iops", "throughput",
            "storage type", "lrs", "zrs", "performance",
        ]
        return any(term in query_lower for term in detail_indicators)

    def _load_resource_index(self, subscription_id: str) -> Dict[str, Dict[str, Any]]:
        """Load raw resources from collector output keyed by normalized resource ID."""
        cached = self._resource_index_cache.get(subscription_id)
        if cached is not None:
            return cached

        resource_index: Dict[str, Dict[str, Any]] = {}
        try:
            resources_path = get_resources_path(subscription_id)
            if path_exists(resources_path):
                raw = read_json(resources_path, default=[])
                resources = raw.get("resources", []) if isinstance(raw, dict) else raw
                if isinstance(resources, list):
                    for resource in resources:
                        if not isinstance(resource, dict):
                            continue
                        resource_id = resource.get("id")
                        if isinstance(resource_id, str) and resource_id.strip():
                            resource_index[resource_id.strip().lower()] = resource
        except Exception as exc:
            LOGGER.debug("Could not load raw resource index for detailed context: %s", exc)

        self._resource_index_cache[subscription_id] = resource_index
        return resource_index

    def _load_node_override_index(self, subscription_id: str) -> Dict[str, Dict[str, Any]]:
        """Load node overrides keyed by normalized resource ID."""
        cached = self._node_override_index_cache.get(subscription_id)
        if cached is not None:
            return cached

        override_index: Dict[str, Dict[str, Any]] = {}
        try:
            node_overrides_path = get_node_overrides_path(subscription_id)
            if path_exists(node_overrides_path):
                raw = read_json(node_overrides_path, default={})
                if isinstance(raw, dict):
                    for resource_id, override in raw.items():
                        if isinstance(resource_id, str) and resource_id.strip() and isinstance(override, dict):
                            override_index[resource_id.strip().lower()] = override
        except Exception as exc:
            LOGGER.debug("Could not load node overrides for detailed context: %s", exc)

        self._node_override_index_cache[subscription_id] = override_index
        return override_index

    def _build_referenced_resource_context(
        self,
        referenced_resource_ids: Optional[List[str]],
        subscription_id: str,
    ) -> str:
        """Build full collector+override context for explicitly referenced resources."""
        if not referenced_resource_ids:
            return "None."

        lines: List[str] = []
        seen: set[str] = set()
        for resource_id in referenced_resource_ids:
            normalized_id = str(resource_id).strip().lower()
            if not normalized_id or normalized_id in seen:
                continue
            seen.add(normalized_id)

            resource_subscription = self._extract_subscription_id_from_resource_id(str(resource_id)) or subscription_id
            resource_index = self._load_resource_index(resource_subscription)
            node_override_index = self._load_node_override_index(resource_subscription)

            raw_resource = resource_index.get(normalized_id)
            node_override = node_override_index.get(normalized_id)

            if not raw_resource and not node_override:
                lines.append(f"- {resource_id}: not found in collector resources or node overrides")
                continue

            payload = {
                "resource_id": str(resource_id).strip(),
                "resource": self._extract_resource_facts(raw_resource) if raw_resource else None,
                "node_override": node_override,
            }
            lines.append(f"- {resource_id}: {json.dumps(payload, ensure_ascii=False)}")

        return "\n".join(lines) if lines else "None."

    def _extract_resource_facts(self, resource: Dict[str, Any]) -> Dict[str, Any]:
        """Extract compact operational facts from a raw collector resource."""
        properties = resource.get("properties") if isinstance(resource.get("properties"), dict) else {}
        sku_data = resource.get("sku") if isinstance(resource.get("sku"), dict) else {}
        hardware_profile = properties.get("hardwareProfile") if isinstance(properties.get("hardwareProfile"), dict) else {}
        storage_profile = properties.get("storageProfile") if isinstance(properties.get("storageProfile"), dict) else {}
        os_disk = storage_profile.get("osDisk") if isinstance(storage_profile.get("osDisk"), dict) else {}
        managed_disk = os_disk.get("managedDisk") if isinstance(os_disk.get("managedDisk"), dict) else {}
        backup_policy = properties.get("backupPolicy") if isinstance(properties.get("backupPolicy"), dict) else {}
        retention_policy = properties.get("retentionPolicy") if isinstance(properties.get("retentionPolicy"), dict) else {}

        facts: Dict[str, Any] = {
            "name": resource.get("name"),
            "type": resource.get("type"),
            "kind": resource.get("kind"),
            "location": resource.get("location"),
        }

        zones_raw = resource.get("zones")
        zones: List[str] = []
        if isinstance(zones_raw, (list, tuple, set)):
            zones = [str(z).strip() for z in zones_raw if str(z).strip()]
        elif zones_raw is not None and str(zones_raw).strip():
            zones = [str(zones_raw).strip()]
        if zones:
            facts["zones"] = zones
            facts["zone_redundant"] = len(zones) >= 2

        if sku_data.get("name"):
            facts["sku_name"] = sku_data.get("name")
        if sku_data.get("tier"):
            facts["sku_tier"] = sku_data.get("tier")

        vm_size = hardware_profile.get("vmSize") or properties.get("vmSize")
        if vm_size:
            facts["vm_size"] = vm_size

        disk_size_gb = (
            properties.get("diskSizeGB")
            or properties.get("diskSizeGb")
            or os_disk.get("diskSizeGB")
            or os_disk.get("diskSizeGb")
        )
        if disk_size_gb is not None:
            facts["disk_size_gb"] = disk_size_gb

        storage_account_type = properties.get("storageAccountType") or managed_disk.get("storageAccountType")
        if storage_account_type:
            facts["storage_account_type"] = storage_account_type

        retention_days = retention_policy.get("retentionDays") or properties.get("retentionDays")
        if retention_days is not None:
            facts["retention_days"] = retention_days

        backup_policy_name = backup_policy.get("name") or properties.get("backupPolicyName")
        if backup_policy_name:
            facts["backup_policy"] = backup_policy_name

        return {key: value for key, value in facts.items() if value is not None and value != ""}

    def _collect_target_resource_ids(
        self,
        query: str,
        graph: Dict[str, Any],
        context: Optional[Dict[str, Any]],
    ) -> List[str]:
        """Find target resource IDs for detailed context extraction."""
        candidates: List[str] = []
        query_lower = (query or "").lower()

        if context and context.get("selected_resource_id"):
            candidates.append(str(context.get("selected_resource_id")))

        query_ids = re.findall(r"/subscriptions/[a-z0-9_\-./]+", query or "", flags=re.IGNORECASE)
        candidates.extend(query_ids)

        if query_lower:
            for node in graph.get("nodes", []):
                if not isinstance(node, dict):
                    continue
                node_id = node.get("id")
                if not node_id:
                    continue
                display_name = self._node_display_name(node)
                if not isinstance(display_name, str) or not display_name.strip():
                    continue
                name_lower = display_name.strip().lower()
                if len(name_lower) < 3:
                    continue
                if name_lower in query_lower:
                    candidates.append(str(node_id))

        if not candidates:
            nodes = graph.get("nodes", [])
            top_nodes = sorted(
                [n for n in nodes if isinstance(n, dict)],
                key=lambda n: self._to_float(self._node_criticality(n), 0.0),
                reverse=True,
            )[:3]
            candidates.extend(str(node.get("id")) for node in top_nodes if node.get("id"))

        deduped: List[str] = []
        seen = set()
        for resource_id in candidates:
            normalized = str(resource_id).strip().lower()
            if normalized and normalized not in seen:
                seen.add(normalized)
                deduped.append(str(resource_id).strip())

        return deduped[:5]

    def _build_detailed_resource_context(
        self,
        query: str,
        graph: Dict[str, Any],
        subscription_id: str,
        context: Optional[Dict[str, Any]],
        force: bool = False,
    ) -> str:
        """Build low-level resource facts section only for detail-heavy queries."""
        if not force and not self._query_requires_resource_details(query):
            return "Not required for this query."

        resource_index = self._load_resource_index(subscription_id)
        if not resource_index:
            return "Detailed resource inventory unavailable."

        target_resource_ids = self._collect_target_resource_ids(query, graph, context)
        if not target_resource_ids:
            return "No target resources identified for detailed context."

        lines: List[str] = []
        for resource_id in target_resource_ids:
            resource = resource_index.get(resource_id.lower())
            if not resource:
                continue
            facts = self._extract_resource_facts(resource)
            if facts:
                lines.append(f"- {resource_id}: {json.dumps(facts, ensure_ascii=False)}")

        if not lines:
            return "No low-level facts found for target resources."

        return "\n".join(lines)

    @staticmethod
    def _build_node_id_catalog(graph: Dict[str, Any], max_ids: int = 250) -> str:
        """Build authoritative node ID catalog for strict ID-only response fields."""
        node_ids: List[str] = []
        for node in graph.get('nodes', []):
            if isinstance(node, dict):
                node_id = node.get('id')
                if isinstance(node_id, str) and node_id.strip():
                    node_ids.append(node_id.strip())

        if not node_ids:
            return "(none)"

        if len(node_ids) > max_ids:
            truncated = node_ids[:max_ids]
            return "\n".join(f"- {node_id}" for node_id in truncated) + "\n- ..."

        return "\n".join(f"- {node_id}" for node_id in node_ids)

    def _canonicalize_llm_references(
        self,
        llm_output: Dict[str, Any],
        graph: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], List[str]]:
        """Resolve compact graph references locally without another model call."""
        alias_candidates: Dict[str, set[str]] = {}
        for node in graph.get('nodes', []):
            if not isinstance(node, dict):
                continue
            canonical_id = str(node.get('id') or '').strip()
            if not canonical_id:
                continue

            metadata = self._node_metadata(node)
            data = self._node_data(node)
            aliases = [
                canonical_id,
                node.get('short_id'),
                node.get('name'),
                metadata.get('display_name'),
                data.get('label'),
            ]
            for alias in aliases:
                normalized_alias = str(alias or '').strip().casefold()
                if normalized_alias:
                    alias_candidates.setdefault(normalized_alias, set()).add(canonical_id)

        reference_map = {
            alias: next(iter(canonical_ids))
            for alias, canonical_ids in alias_candidates.items()
            if len(canonical_ids) == 1
        }

        unresolved: List[str] = []

        def resolve(value: Any, field_name: str) -> Optional[str]:
            normalized_value = str(value or '').strip().casefold()
            canonical_id = reference_map.get(normalized_value)
            if canonical_id:
                return canonical_id
            unresolved.append(f"{field_name}: {value}")
            return None

        normalized_output = dict(llm_output)
        normalized_resources: List[str] = []
        seen_resources: set[str] = set()
        resources_raw = llm_output.get('resources_to_highlight', [])
        resources = resources_raw if isinstance(resources_raw, list) else [resources_raw]
        for resource_id in resources:
            canonical_id = resolve(resource_id, "resources_to_highlight")
            if canonical_id and canonical_id.casefold() not in seen_resources:
                seen_resources.add(canonical_id.casefold())
                normalized_resources.append(canonical_id)
        normalized_output['resources_to_highlight'] = normalized_resources

        normalized_insights: List[Dict[str, Any]] = []
        insights_raw = llm_output.get('criticality_insights', [])
        insights = insights_raw if isinstance(insights_raw, list) else [insights_raw]
        for insight in insights:
            if not isinstance(insight, dict):
                continue
            canonical_id = resolve(insight.get('node_id'), "criticality_insights.node_id")
            if canonical_id:
                normalized_insights.append({**insight, 'node_id': canonical_id})
        normalized_output['criticality_insights'] = normalized_insights

        normalized_edges: List[Dict[str, Any]] = []
        edges_raw = llm_output.get('suggested_edges', [])
        edges = edges_raw if isinstance(edges_raw, list) else [edges_raw]
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            source = resolve(edge.get('source'), "suggested_edges.source")
            target = resolve(edge.get('target'), "suggested_edges.target")
            if source and target:
                normalized_edges.append({**edge, 'source': source, 'target': target})
        normalized_output['suggested_edges'] = normalized_edges

        return normalized_output, unresolved

    def _repair_terraform_output(
        self,
        *,
        llm_output: Dict[str, Any],
        terraform_validation: str,
        graph: Dict[str, Any],
        agent_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """One-shot repair pass for terraform outputs that fail validation checks."""
        repair_system_prompt = self._system_prompt('terraform', flow='terraform')
        failed_findings_summary = self._summarize_failed_findings(
            graph,
            max_resources=20,
            max_checks_per_resource=2,
            include_long_description=False,
            include_benefits=False,
            include_links=False,
        )
        repair_user_prompt = f"""
Fix the Terraform output so it satisfies the required failed findings and resolves all listed validation issues.

Validation issues to fix:
{terraform_validation}

Authoritative failed findings:
{failed_findings_summary}

Previous JSON output:
{json.dumps(llm_output, ensure_ascii=False)}

Return corrected JSON only, preserving the same response schema.
Keep unrelated resources and settings unchanged.
Apply only the minimum changes required to satisfy validation issues.
Do not invent resources, module names, or unsupported fields.
"""

        try:
            repaired = self.llm_gateway.generate_json(
                system_prompt=repair_system_prompt,
                user_prompt=repair_user_prompt,
                temperature=0.0,
                max_tokens=self.llm_generation_config.get('max_tokens', 2000),
                model=self.llm_generation_config.get('model'),
                agent_id=agent_id,
                conversation_id=conversation_id,
            )
            return repaired if isinstance(repaired, dict) else None
        except Exception as error:
            LOGGER.warning("Terraform repair pass failed; continuing with original output: %s", error)
            return None

    def _build_llm_baseline_summary(self, graph: Dict[str, Any]) -> str:
        """Summarize existing LLM annotations to ground chat in prior analysis."""
        llm_annotations = graph.get("llm_annotations") or {}
        nodes_ann = llm_annotations.get("nodes") or []
        edges_ann = llm_annotations.get("edges") or []

        if not nodes_ann and not edges_ann:
            return "No prior LLM annotations available."

        nodes_by_id = {n.get("id"): n for n in graph.get("nodes", []) if isinstance(n, dict)}

        node_lines = []
        for item in nodes_ann[:10]:
            node_id = item.get("node_id")
            ann = item.get("annotations") or {}
            node = nodes_by_id.get(node_id, {})
            name = ann.get("display_name") or self._node_display_name(node) or node_id
            service = ann.get("azure_service_name") or self._node_resource_type(node)
            score = self._first_non_empty(ann.get("criticality_score"), self._node_criticality(node))
            reason = ann.get("reason")
            line = f"- {name} ({service})"
            if score is not None:
                line += f" [criticality: {score}]"
            if reason:
                line += f" - {reason}"
            node_lines.append(line)

        edge_lines = []
        for edge in edges_ann[:10]:
            source = edge.get("source")
            target = edge.get("target")
            rel = edge.get("relationship") or "relates_to"
            conf = edge.get("confidence")
            reason = edge.get("reason")
            line = f"- {source} --[{rel}]--> {target}"
            if conf is not None:
                line += f" (confidence: {conf:.2f})"
            if reason:
                line += f" - {reason}"
            edge_lines.append(line)

        summary_parts = []
        if node_lines:
            summary_parts.append("Node annotations:\n" + "\n".join(node_lines))
        if edge_lines:
            summary_parts.append("Edge suggestions:\n" + "\n".join(edge_lines))

        return "\n\n".join(summary_parts) if summary_parts else "No prior LLM annotations available."

    def _extract_llm_references(self, graph: Dict[str, Any]) -> List[Dict[str, str]]:
        """Extract referenced node ids from LLM annotations with labels."""
        llm_annotations = graph.get("llm_annotations") or {}
        nodes_ann = llm_annotations.get("nodes") or []
        edges_ann = llm_annotations.get("edges") or []

        nodes_by_id = {n.get("id"): n for n in graph.get("nodes", []) if isinstance(n, dict)}
        seen: set[str] = set()
        references: List[Dict[str, str]] = []

        def add_ref(node_id: Optional[str]) -> None:
            if not node_id or node_id in seen:
                return
            node = nodes_by_id.get(node_id, {})
            label = self._node_display_name(node) or node_id
            references.append({"id": node_id, "label": label})
            seen.add(node_id)

        for item in nodes_ann:
            if isinstance(item, dict):
                add_ref(item.get("node_id"))

        for edge in edges_ann:
            if not isinstance(edge, dict):
                continue
            add_ref(edge.get("source"))
            add_ref(edge.get("target"))

        references.sort(key=lambda r: r.get("label", ""))
        return references

    def _summarize_failed_findings(
        self,
        graph: Dict[str, Any],
        *,
        max_resources: int = 999,
        max_checks_per_resource: int = 3,
        include_long_description: bool = True,
        include_benefits: bool = True,
        include_links: bool = True,
    ) -> str:
        """Summarize failed findings from resilience evaluations with configurable verbosity."""
        evaluations = (graph.get("resilience_evaluations") or {}).get("evaluations", {})
        failed_items: List[str] = []
        nodes_by_id = {n.get("id"): n for n in graph.get("nodes", []) if isinstance(n, dict)}
        omitted_resources = 0

        for resource_id, payload in evaluations.items():
            checks = payload.get("checks", []) if isinstance(payload, dict) else []
            failed_checks = [c for c in checks if isinstance(c, dict) and c.get("status") == "fail"]
            if not failed_checks:
                continue

            if len(failed_items) >= max_resources:
                omitted_resources += 1
                continue

            node = nodes_by_id.get(resource_id, {})
            name = self._node_display_name(node)
            if not name or name.strip().lower() == "unknown":
                name = str(resource_id).strip() or "scope-level finding"

            resource_type = self._node_resource_type(node)
            if not resource_type or resource_type.strip().lower() == "unknown":
                inferred_type = ""
                if isinstance(payload, dict):
                    inferred_type = str(payload.get("resource_type") or payload.get("type") or "").strip()
                resource_type = inferred_type or "scope_level"

            # Extract detailed check information for each failed check
            check_details = []
            for check in failed_checks[:max_checks_per_resource]:
                # Extract problem description and context
                description = check.get("description", "")
                long_description = check.get("long_description", "").strip()
                impact = check.get("impact", "").lower()
                potential_benefits = check.get("potential_benefits", "").strip()
                learn_more = check.get("learn_more", {})
                recommendation_id = str(check.get("recommendation_id") or "").strip()

                # Build detailed check summary
                detail_parts = [description]
                if include_long_description and long_description:
                    # Take first 150 chars of long description for context
                    context_snippet = long_description.replace("\n", " ")[:150]
                    detail_parts.append(f"({context_snippet}...)")

                detail = " ".join(detail_parts)
                if recommendation_id:
                    detail += f" [recommendation_id: {recommendation_id}]"
                if impact:
                    detail += f" [Impact: {impact}]"
                
                # Add benefits for context
                if include_benefits and potential_benefits:
                    detail += f" Benefits: {potential_benefits[:100]}..."
                
                # Add Microsoft Learn reference if available
                if include_links and learn_more and isinstance(learn_more, dict):
                    links = learn_more.get("links", [])
                    if links:
                        learn_urls = [link.get("url") for link in links if isinstance(link, dict) and link.get("url")]
                        if learn_urls:
                            detail += f" | See: {learn_urls[0]}"
                
                check_details.append(detail)

            # Format resource entry with all failed check details
            if check_details:
                resource_entry = f"- {name} ({resource_type}):\n"
                for detail in check_details:
                    resource_entry += f"  * {detail}\n"
                failed_items.append(resource_entry)
            else:
                failed_items.append(f"- {name} ({resource_type}): Check failed")

        if not failed_items:
            return "No failed findings available."

        if omitted_resources > 0:
            failed_items.append(f"- ... and {omitted_resources} more resources with failed findings.")

        return "\n".join(failed_items)

    @staticmethod
    def _normalize_recommendation_text(value: Any) -> str:
        return str(value or "").strip().lower()

    def _build_recommendation_id_catalog(self, graph: Dict[str, Any], max_ids: int = 250) -> str:
        """Build authoritative recommendation ID catalog for strict recommendation matching."""
        authoritative = self._extract_authoritative_recommendations(graph)
        recommendation_ids = sorted(authoritative.keys(), key=lambda rid: rid.lower())

        if not recommendation_ids:
            return "(none)"

        if len(recommendation_ids) > max_ids:
            truncated = recommendation_ids[:max_ids]
            return "\n".join(f"- {recommendation_id}" for recommendation_id in truncated) + "\n- ..."

        return "\n".join(f"- {recommendation_id}" for recommendation_id in recommendation_ids)

    def _extract_authoritative_recommendations(self, graph: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Build authoritative recommendation catalog from resilience evaluations."""
        evaluations = (graph.get("resilience_evaluations") or {}).get("evaluations", {})
        catalog: Dict[str, Dict[str, Any]] = {}

        for payload in evaluations.values():
            if not isinstance(payload, dict):
                continue
            checks = payload.get("checks", [])
            if not isinstance(checks, list):
                continue

            for check in checks:
                if not isinstance(check, dict):
                    continue
                recommendation_id = str(check.get("recommendation_id") or "").strip()
                if not recommendation_id:
                    continue

                item = catalog.get(recommendation_id)
                if item is None:
                    item = {
                        "recommendation_id": recommendation_id,
                        "title": str(check.get("description") or recommendation_id),
                        "description": str(check.get("description") or ""),
                        "category": check.get("category"),
                        "impact": check.get("impact"),
                        "potential_benefits": check.get("potential_benefits"),
                        "learn_more": check.get("learn_more"),
                        "failed_count": 0,
                        "pending_count": 0,
                        "pass_count": 0,
                    }
                    catalog[recommendation_id] = item

                status = self._normalize_recommendation_text(check.get("status"))
                if status == "fail":
                    item["failed_count"] = int(item.get("failed_count") or 0) + 1
                elif status == "pending":
                    item["pending_count"] = int(item.get("pending_count") or 0) + 1
                elif status == "pass":
                    item["pass_count"] = int(item.get("pass_count") or 0) + 1

                if not item.get("description") and check.get("description"):
                    item["description"] = str(check.get("description"))
                    item["title"] = str(check.get("description"))
                if not item.get("category") and check.get("category"):
                    item["category"] = check.get("category")
                if not item.get("impact") and check.get("impact"):
                    item["impact"] = check.get("impact")
                if not item.get("potential_benefits") and check.get("potential_benefits"):
                    item["potential_benefits"] = check.get("potential_benefits")
                if not item.get("learn_more") and check.get("learn_more"):
                    item["learn_more"] = check.get("learn_more")

        return catalog

    def _normalize_recommendations(
        self,
        recommendations_raw: Any,
        graph: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Normalize LLM recommendations against authoritative resilience recommendations using ID-only matching."""
        authoritative = self._extract_authoritative_recommendations(graph)
        if not authoritative:
            return []

        recommendations_list = recommendations_raw if isinstance(recommendations_raw, list) else [recommendations_raw]
        selected_ids: List[str] = []
        seen_ids: set[str] = set()

        def add_selected_id(rec_id: str) -> None:
            normalized_id = self._normalize_recommendation_text(rec_id)
            if not normalized_id:
                return
            matched_id = next((k for k in authoritative.keys() if self._normalize_recommendation_text(k) == normalized_id), None)
            if not matched_id or matched_id in seen_ids:
                return
            selected_ids.append(matched_id)
            seen_ids.add(matched_id)

        for rec in recommendations_list:
            rec_id = ""

            if isinstance(rec, dict):
                rec_id = str(rec.get("recommendation_id") or rec.get("id") or "").strip()

            if rec_id:
                add_selected_id(rec_id)

        if not selected_ids:
            ranked_authoritative = sorted(
                authoritative.values(),
                key=lambda item: (
                    -(int(item.get("failed_count") or 0)),
                    -(int(item.get("pending_count") or 0)),
                    self._normalize_recommendation_text(item.get("title")),
                ),
            )
            for item in ranked_authoritative:
                rec_id = str(item.get("recommendation_id") or "").strip()
                if not rec_id or rec_id in seen_ids:
                    continue
                selected_ids.append(rec_id)
                seen_ids.add(rec_id)
                if len(selected_ids) >= 5:
                    break

        result: List[Dict[str, Any]] = []
        for recommendation_id in selected_ids:
            item = authoritative.get(recommendation_id)
            if not item:
                continue
            result.append({
                "recommendation_id": item.get("recommendation_id"),
                "title": item.get("title") or item.get("description") or item.get("recommendation_id"),
                "description": item.get("description") or item.get("title") or item.get("recommendation_id"),
                "category": item.get("category"),
                "impact": item.get("impact"),
                "potential_benefits": item.get("potential_benefits"),
                "learn_more": item.get("learn_more"),
                "failed_count": item.get("failed_count", 0),
                "pending_count": item.get("pending_count", 0),
                "pass_count": item.get("pass_count", 0),
            })

        return result

    def get_baseline_summary(self, graph: Dict[str, Any]) -> str:
        """Public helper to build baseline summary for chat initialization."""
        normalized = self._normalize_graph(graph)
        return self._build_llm_baseline_summary(normalized)

    def get_baseline(self, graph: Dict[str, Any]) -> Dict[str, Any]:
        """Return baseline summary and references for chat initialization."""
        normalized = self._normalize_graph(graph)
        summary = self._build_llm_baseline_summary(normalized)
        references = self._extract_llm_references(normalized)
        return {
            "summary": summary,
            "references": references,
            "reference_count": len(references),
        }

    def _summarize_nodes(self, nodes: List[Dict[str, Any]]) -> str:
        """Create concise node summary."""
        if not nodes:
            return "No nodes available"

        max_nodes = 15
        # Sort by criticality
        sorted_nodes = sorted(
            nodes,
            key=lambda n: self._to_float(self._node_criticality(n), 0.0),
            reverse=True
        )[:max_nodes]

        summary = []
        for node in sorted_nodes:
            label = self._node_display_name(node)
            resource_type = self._node_resource_type(node)
            criticality = self._first_non_empty(self._node_criticality(node), 'unknown')
            line = f"- {label} ({resource_type}) [Criticality: {criticality}]"
            region = self._node_region(node)
            if region:
                line += f" [Region: {region}]"
            zones = self._node_zones(node)
            if zones:
                line += f" [Zones: {','.join(zones)}]"
            summary.append(line)

        if len(nodes) > max_nodes:
            summary.append(f"- (+{len(nodes) - max_nodes} more resources not shown)")

        return '\n'.join(summary) if summary else "No nodes available"

    def _summarize_edges(
        self,
        edges: List[Dict[str, Any]],
        nodes: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Create concise edge summary using display names for readability."""
        if not edges:
            return "No relationships found"

        name_by_id: Dict[str, str] = {}
        for node in nodes or []:
            if isinstance(node, dict) and node.get('id'):
                name_by_id[str(node['id'])] = self._node_display_name(node)

        def label_for(node_id: Any) -> str:
            key = str(node_id) if node_id is not None else '...'
            return name_by_id.get(key, key)

        max_edges = 25
        summary = []
        for edge in edges[:max_edges]:
            source = label_for(edge.get('source'))
            target = label_for(edge.get('target'))
            rel = self._edge_relationship(edge)
            confidence = self._edge_confidence(edge, 1.0)
            summary.append(f"- {source} --[{rel}:{confidence:.2f}]--> {target}")

        if len(edges) > max_edges:
            summary.append(f"- (+{len(edges) - max_edges} more relationships not shown)")

        return '\n'.join(summary) if summary else "No relationships found"

    def _summarize_resilience_groups(self, graph: Dict[str, Any], max_groups: int = 15) -> str:
        """Summarize resilience/HA correlation groups so the model avoids redundant advice."""
        groups = graph.get('groups') or []
        if not isinstance(groups, list) or not groups:
            return "No resilience groups identified."

        nodes_by_id = {n.get('id'): n for n in graph.get('nodes', []) if isinstance(n, dict)}

        lines: List[str] = []
        for group in groups[:max_groups]:
            if not isinstance(group, dict):
                continue
            name = str(group.get('name') or group.get('id') or 'group').strip()
            member_ids = group.get('nodes') or []
            if not isinstance(member_ids, list):
                member_ids = []
            member_names = [
                self._node_display_name(nodes_by_id[mid])
                for mid in member_ids
                if mid in nodes_by_id
            ]
            shown = member_names[:6]
            members_text = ", ".join(shown)
            if len(member_names) > len(shown):
                members_text += f", +{len(member_names) - len(shown)} more"
            lines.append(f"- {name} ({len(member_ids)} members): {members_text}".rstrip(": ").rstrip())

        if len(groups) > max_groups:
            lines.append(f"- (+{len(groups) - max_groups} more groups not shown)")

        return '\n'.join(lines) if lines else "No resilience groups identified."

    def _extract_terraform_code(self, llm_output: Dict[str, Any]) -> Optional[str]:
        """Extract terraform code from LLM output, converting dict to HCL string if needed."""
        # Try standard keys
        terraform_code = (
            llm_output.get('terraform_code')
            or llm_output.get('terraform')
            or llm_output.get('code')
        )

        # Compiler-agent schema: files=[{filename, content}, ...]
        if not terraform_code:
            files_raw = llm_output.get('files')
            if isinstance(files_raw, list):
                rendered_files: List[str] = []
                for item in files_raw:
                    if not isinstance(item, dict):
                        continue
                    filename = str(item.get('filename') or '').strip()
                    content = item.get('content')
                    if not filename or not isinstance(content, str) or not content.strip():
                        continue
                    rendered_files.append(f"# {filename}\n{content.strip()}")
                if rendered_files:
                    return "\n\n".join(rendered_files)

        # If it's a dict/structured format, convert to HCL string
        if isinstance(terraform_code, dict):
            return self._dict_to_hcl(terraform_code)
        
        # If it's already a string, return it
        if isinstance(terraform_code, str):
            return terraform_code if terraform_code.strip() else None
        
        return None

    def _validate_terraform_against_findings(
        self,
        terraform_code: Optional[str],
        graph: Dict[str, Any],
        llm_output: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Validate generated Terraform code against failed findings and best practices.
        Returns validation warnings if code has issues.
        Returns None if validation passes.
        """
        if not terraform_code:
            return None
        
        code_lower = terraform_code.lower()
        issues = []

        if llm_output is not None:
            backend_evidence = llm_output.get("_backend_retrieval_evidence")
            evidence_refs = {
                str(item.get("ref") or "").strip()
                for item in (backend_evidence if isinstance(backend_evidence, list) else [])
                if isinstance(item, dict) and str(item.get("ref") or "").strip()
            }

            rag_trace = llm_output.get("rag_trace")
            retrieval_status = (
                str(rag_trace.get("retrieval_status") or "").strip().casefold()
                if isinstance(rag_trace, dict)
                else ""
            )

            module_decisions = llm_output.get("module_decisions")
            decisions = module_decisions if isinstance(module_decisions, list) else []
            if not decisions:
                issues.append("Terraform generation requires at least one explicit module or native-resource decision.")
            module_choices = [
                decision
                for decision in decisions
                if isinstance(decision, dict)
                and str(decision.get("decision") or "").strip().casefold() in {"avm", "caf"}
            ]
            if module_choices and not evidence_refs:
                issues.append("AVM/CAF module decisions require successful backend catalog retrieval.")
            if module_choices and retrieval_status not in {"grounded", "partial"}:
                issues.append(
                    "AVM/CAF module decisions require rag_trace.retrieval_status to be grounded or partial."
                )
            for decision in decisions:
                if not isinstance(decision, dict):
                    continue
                decision_type = str(decision.get("decision") or "").strip().casefold()
                source_refs = decision.get("source_refs")
                populated_refs = [
                    str(ref).strip()
                    for ref in (source_refs if isinstance(source_refs, list) else [])
                    if str(ref).strip()
                ]
                if decision_type in {"avm", "caf"} and not populated_refs:
                    target = str(decision.get("target") or decision.get("module_or_resource") or "unknown target")
                    issues.append(f"AVM/CAF decision for {target} requires at least one concrete source reference.")
                elif populated_refs and any(ref not in evidence_refs for ref in populated_refs):
                    target = str(decision.get("target") or decision.get("module_or_resource") or "unknown target")
                    issues.append(
                        f"Terraform decision for {target} contains a source reference that was not returned "
                        "by backend retrieval."
                    )

            files = llm_output.get("files")
            for item in (files if isinstance(files, list) else []):
                if not isinstance(item, dict):
                    continue
                filename = str(item.get("filename") or "").strip()
                content = item.get("content")
                if not filename.endswith(".tf") or not isinstance(content, str) or not content.strip():
                    continue
                try:
                    hcl2.loads(content)
                except Exception:  # noqa: BLE001
                    issues.append(f"Generated file {filename} is not valid HCL.")
        
        # Check for FromImage without source_image_reference
        if "create_option" in code_lower and "fromimage" in code_lower:
            # Check if source_image_reference exists in the code
            if "source_image_reference" not in code_lower:
                issues.append(
                    "⚠️ Code uses create_option = \"FromImage\" but doesn't specify source_image_reference. "
                    "Either add source_image_reference block with the image details, or use create_option = \"Empty\" for new disks, "
                    "or create_option = \"Copy\" with source_resource_id to migrate an existing disk."
                )
        
        # Check for zone+ZRS contradiction (zones parameter makes disk zone-pinned, not zone-redundant)
        if "zones = [" in code_lower or 'zones = ["' in code_lower:
            if "_zrs" in code_lower:
                issues.append(
                    "⚠️ Code uses 'zones' parameter with ZRS storage type. Zone-Redundant Storage automatically replicates "
                    "across all availability zones—the 'zones' parameter pins it to specific zones, contradicting ZRS. "
                    "Remove the 'zones' parameter when using ZRS storage types."
                )
        
        # Check if disk is zone-pinned (LRS/single zone) when ZRS findings exist
        evaluations = (graph.get("resilience_evaluations") or {}).get("evaluations", {})
        failed_descriptions = [
            str(check.get("description") or "").casefold()
            for payload in evaluations.values()
            for check in (payload.get("checks", []) if isinstance(payload, dict) else [])
            if isinstance(check, dict) and check.get("status") == "fail"
        ]
        has_model_deployment_finding = any(
            "model" in description and "deployment" in description
            for description in failed_descriptions
        )
        if has_model_deployment_finding and "azurerm_cognitive_deployment" not in code_lower:
            issues.append(
                "Failed Azure OpenAI model-deployment controls require azurerm_cognitive_deployment changes; "
                "creating or updating only azurerm_cognitive_account does not remediate deployment SKU/mode findings."
            )

        has_global_standard_finding = any(
            "global standard" in description
            for description in failed_descriptions
        )
        if has_global_standard_finding and "globalstandard" not in code_lower:
            issues.append(
                "The failed Azure OpenAI Global Standard control requires an azurerm_cognitive_deployment "
                'with scale.type = "GlobalStandard".'
            )

        has_global_provisioned_finding = any(
            "global provisioned" in description
            for description in failed_descriptions
        )
        if has_global_provisioned_finding and "globalprovisionedmanaged" not in code_lower:
            issues.append(
                "The failed Azure OpenAI Global Provisioned control requires an azurerm_cognitive_deployment "
                'with scale.type = "GlobalProvisionedManaged".'
            )

        has_search_replica_finding = any(
            "search" in description and "multiple replicas" in description
            for description in failed_descriptions
        )
        replica_counts = [int(value) for value in re.findall(r"replica_count\s*=\s*(\d+)", code_lower)]
        replica_variable_names = re.findall(r"replica_count\s*=\s*var\.([a-z0-9_]+)", code_lower)
        replica_variable_defaults = [
            int(default)
            for variable_name in replica_variable_names
            for default in re.findall(
                rf'variable\s+"{re.escape(variable_name)}"\s*\{{[^}}]*?default\s*=\s*(\d+)',
                code_lower,
                flags=re.DOTALL,
            )
        ]
        if has_search_replica_finding and not any(
            value >= 2 for value in replica_counts + replica_variable_defaults
        ):
            issues.append(
                "The failed AI Search availability control requires replica_count >= 2, either as a literal "
                "or through a variable whose default is at least 2."
            )

        has_search_multi_region_finding = any(
            "multi region" in description and "search" in description
            for description in failed_descriptions
        )
        search_service_count = len(re.findall(r'resource\s+"azurerm_search_service"', code_lower))
        has_global_routing = any(
            resource_type in code_lower
            for resource_type in (
                "azurerm_cdn_frontdoor_profile",
                "azurerm_traffic_manager_profile",
                "azurerm_frontdoor",
            )
        )
        if has_search_multi_region_finding and (search_service_count < 2 or not has_global_routing):
            issues.append(
                "The failed AI Search multi-region control requires at least two regional search services "
                "and explicit global failover routing."
            )

        has_service_health_finding = any(
            "service health alert" in description
            for description in failed_descriptions
        )
        if has_service_health_finding and not (
            "azurerm_monitor_activity_log_alert" in code_lower and "servicehealth" in code_lower
        ):
            issues.append(
                "The failed Service Health control requires an azurerm_monitor_activity_log_alert "
                "with a ServiceHealth category criterion."
            )

        has_region_alignment_finding = any(
            "resource group" in description and "same region" in description
            for description in failed_descriptions
        )
        if has_region_alignment_finding:
            region_alignment_valid = False
            try:
                parsed = hcl2.loads(terraform_code)
                variables = {
                    name.strip('"'): body.get("default")
                    for variable in parsed.get("variable", [])
                    for name, body in variable.items()
                    if isinstance(body, dict) and "default" in body
                }
                resources = []
                for resource in parsed.get("resource", []):
                    for resource_type, instances in resource.items():
                        for resource_name, body in instances.items():
                            resources.append((resource_type.strip('"'), resource_name.strip('"'), body))

                resource_groups = {
                    name: body
                    for resource_type, name, body in resources
                    if resource_type == "azurerm_resource_group"
                }

                def resolve_location(value: Any) -> Any:
                    variable_match = re.fullmatch(r"\$\{var\.([a-zA-Z0-9_]+)\}", str(value))
                    if variable_match:
                        return variables.get(variable_match.group(1), value)
                    group_match = re.fullmatch(
                        r"\$\{azurerm_resource_group\.([a-zA-Z0-9_-]+)\.location\}",
                        str(value),
                    )
                    if group_match and group_match.group(1) in resource_groups:
                        return resolve_location(resource_groups[group_match.group(1)].get("location"))
                    return value

                aligned_resources = 0
                mismatched_resources = []
                for resource_type, resource_name, body in resources:
                    if resource_type == "azurerm_resource_group" or not isinstance(body, dict):
                        continue
                    group_reference = str(body.get("resource_group_name", ""))
                    match = re.fullmatch(
                        r"\$\{azurerm_resource_group\.([a-zA-Z0-9_-]+)\.name\}",
                        group_reference,
                    )
                    if not match or match.group(1) not in resource_groups or "location" not in body:
                        continue
                    resource_location = resolve_location(body["location"])
                    group_location = resolve_location(resource_groups[match.group(1)].get("location"))
                    if resource_location == group_location:
                        aligned_resources += 1
                    else:
                        mismatched_resources.append(f"{resource_type}.{resource_name}")

                region_alignment_valid = bool(resource_groups) and aligned_resources > 0 and not mismatched_resources
                if mismatched_resources:
                    issues.append(
                        "Region alignment is invalid for resources whose location differs from their referenced "
                        f"resource group: {', '.join(mismatched_resources)}."
                    )
            except Exception:
                logger.debug("Could not parse Terraform for region-alignment validation", exc_info=True)

            if not region_alignment_valid and not any("Region alignment is invalid" in issue for issue in issues):
                issues.append(
                    "The failed region alignment control requires at least one azurerm_resource_group declaration and "
                    "resources whose location matches the referenced resource group's location."
                )

        has_zrs_finding = any(
            "zone-redundant" in (str(check.get("description", "")).lower() or "")
            or "zrs" in str(check.get("description", "")).lower()
            for payload in evaluations.values()
            for check in (payload.get("checks", []) if isinstance(payload, dict) else [])
            if isinstance(check, dict) and check.get("status") == "fail"
        )
        
        if has_zrs_finding:
            # Check if code actually uses LRS (bad) vs ZRS (good)
            has_lrs = "premium_lrs" in code_lower or ("standard" in code_lower and "_lrs" in code_lower and "_zrs" not in code_lower)
            has_zrs = "_zrs" in code_lower
            
            if has_lrs and not has_zrs:
                issues.append(
                    "⚠️ Code uses LRS (Locally Redundant Storage) but the finding requires ZRS (Zone-Redundant Storage). "
                    "Change storage_account_type to use _ZRS suffix (e.g., Premium_ZRS, StandardSSD_ZRS, Standard_ZRS)."
                )
        
        return "\n\n".join(issues) if issues else None

    def _dict_to_hcl(self, obj: Any, indent: int = 0) -> str:
        """Convert a structured dict/JSON object to HCL string representation."""
        if not isinstance(obj, dict):
            return json.dumps(obj) if not isinstance(obj, str) else f'"{obj}"'
        
        lines = []
        indent_str = "  " * indent
        next_indent_str = "  " * (indent + 1)

        for key, value in obj.items():
            if isinstance(value, dict):
                # Handle nested objects
                lines.append(f'{indent_str}{key} = ' + "{")
                for sub_key, sub_value in value.items():
                    if isinstance(sub_value, dict):
                        lines.append(f'{next_indent_str}{sub_key} = ' + "{")
                        for k, v in sub_value.items():
                            if isinstance(v, dict):
                                lines.append(f'{next_indent_str}  {k} = ' + "{")
                                for mk, mv in v.items():
                                    if isinstance(mv, (list, dict)):
                                        lines.append(f'{next_indent_str}    {mk} = {json.dumps(mv)}')
                                    else:
                                        lines.append(f'{next_indent_str}    {mk} = {json.dumps(mv)}')
                                lines.append(f'{next_indent_str}  ' + "}")
                            elif isinstance(v, list):
                                lines.append(f'{next_indent_str}  {k} = {json.dumps(v)}')
                            else:
                                lines.append(f'{next_indent_str}  {k} = {json.dumps(v)}')
                        lines.append(f'{next_indent_str}' + "}")
                    elif isinstance(sub_value, list):
                        lines.append(f'{next_indent_str}{sub_key} = {json.dumps(sub_value)}')
                    else:
                        lines.append(f'{next_indent_str}{sub_key} = {json.dumps(sub_value)}')
                lines.append(f'{indent_str}' + "}")
            elif isinstance(value, list):
                lines.append(f'{indent_str}{key} = {json.dumps(value)}')
            else:
                lines.append(f'{indent_str}{key} = {json.dumps(value)}')

        return '\n'.join(lines)

    def _is_logical_edge(self, source_id: str, target_id: str, relationship: str, nodes_by_id: Dict[str, Any]) -> bool:
        """
        Validate that an edge suggestion is logically sensible.
        
        Filters out obviously illogical suggestions like:
        - Connecting two disks from different VMs with 'depends_on'
        - Connecting the same resource to itself
        - Connecting unrelated resources with strong relationships
        
        Returns:
            True if edge is logical, False if should be filtered out
        """
        # Rule 1: A resource cannot depend on itself
        if source_id == target_id:
            LOGGER.debug(f"Filtered self-loop edge: {source_id} -> {source_id}")
            return False
        
        source_node = nodes_by_id.get(source_id, {})
        target_node = nodes_by_id.get(target_id, {})

        source_type = self._node_resource_type(source_node).lower()
        target_type = self._node_resource_type(target_node).lower()
        
        # Rule 2: Storage disks from different VMs shouldn't have depends_on
        # Example: managed_disks from different VMs
        if relationship == 'depends_on':
            # Extract parent VM from the resource ID
            source_vm = self._extract_parent_resource(source_id, 'virtualMachines')
            target_vm = self._extract_parent_resource(target_id, 'virtualMachines')
            
            # If both are disks and belong to different VMs, this is illogical
            if source_type in ['microsoft.compute/disks', 'managed_disk'] and \
               target_type in ['microsoft.compute/disks', 'managed_disk']:
                if source_vm and target_vm and source_vm != target_vm:
                    LOGGER.debug(
                        f"Filtered illogical disk dependency: disk from VM {source_vm} "
                        f"depends on disk from VM {target_vm}"
                    )
                    return False
        
        # Rule 3: NICs from different VMs shouldn't have depends_on
        if relationship == 'depends_on':
            source_vm = self._extract_parent_resource(source_id, 'virtualMachines')
            target_vm = self._extract_parent_resource(target_id, 'virtualMachines')
            
            if source_type in ['microsoft.network/networkinterfaces', 'network_interface'] and \
               target_type in ['microsoft.network/networkinterfaces', 'network_interface']:
                if source_vm and target_vm and source_vm != target_vm:
                    LOGGER.debug(
                        f"Filtered illogical NIC dependency: NIC from VM {source_vm} "
                        f"depends on NIC from VM {target_vm}"
                    )
                    return False
        
        # Rule 4: Log suspiciously high-confidence edges for monitoring
        # (These often hide errors)
        
        return True
    
    def _extract_parent_resource(self, resource_id: str, parent_type: str) -> Optional[str]:
        """
        Extract parent resource ID from a resource path.
        
        Example:
            Input: '/subscriptions/sub/resourceGroups/rg/providers/microsoft.compute/
                    virtualMachines/vm1/storageProfile/osDisk'
            parent_type: 'virtualMachines'
            Output: '/subscriptions/sub/resourceGroups/rg/providers/microsoft.compute/virtualMachines/vm1'
        """
        try:
            # Split by the parent type
            if parent_type.lower() not in resource_id.lower():
                return None
            
            # Find the parent type in the path (case-insensitive)
            import re
            pattern = f"(?i){re.escape(parent_type)}/[^/]+"
            match = re.search(pattern, resource_id)
            
            if match:
                # Extract up to and including the resource name
                start = resource_id.lower().find(parent_type.lower())
                # Find the next '/' after the resource name
                remaining = resource_id[start + len(parent_type) + 1:]
                next_slash = remaining.find('/')
                
                if next_slash == -1:
                    # No more slashes, this is the end of the parent resource
                    resource_name = remaining.split('/')[0]
                    return resource_id[:start + len(parent_type) + 1 + len(resource_name)]
                else:
                    resource_name = remaining[:next_slash]
                    return resource_id[:start + len(parent_type) + 1 + len(resource_name)]
        except Exception as e:
            LOGGER.debug(f"Error extracting parent resource from {resource_id}: {e}")
        
        return None

    @staticmethod
    def _normalize_relationship_name(value: Any) -> str:
        normalized = str(value or "related").strip().lower()
        return normalized or "related"

    def _build_existing_edge_index(
        self,
        graph: Dict[str, Any],
    ) -> Tuple[set[Tuple[str, str]], set[Tuple[str, str, str]]]:
        """Build normalized lookup sets for existing graph edges."""
        pair_keys: set[Tuple[str, str]] = set()
        relationship_keys: set[Tuple[str, str, str]] = set()

        for existing_edge in graph.get('edges', []) or []:
            if not isinstance(existing_edge, dict):
                continue

            source = str(existing_edge.get('source') or '').strip().lower()
            target = str(existing_edge.get('target') or '').strip().lower()
            if not source or not target:
                continue

            relationship = self._normalize_relationship_name(
                self._edge_relationship(existing_edge)
            )

            pair_keys.add((source, target))
            relationship_keys.add((source, target, relationship))

        return pair_keys, relationship_keys

    def _process_llm_response(
        self,
        llm_output: Dict[str, Any],
        graph: Dict[str, Any],
        llm_metrics: Optional[Dict[str, Any]] = None,
        *,
        query: str,
        query_type: str,
        flow: str,
        include_rag_trace: bool,
        target_agent_id: str,
        scope_type: str,
        scope_id: str,
        conversation_id: Optional[str],
        trace_id: str,
    ) -> ChatResponse:
        """Validate and process LLM response against guardrails."""
        sources: List[ChatSource] = []
        sources_raw = llm_output.get('sources', [])
        sources_list = sources_raw if isinstance(sources_raw, list) else [sources_raw]
        for src in sources_list:
            if isinstance(src, str):
                url = src.strip()
                if _is_allowed_source_url(url):
                    sources.append(ChatSource(url=url))
                continue
            if not isinstance(src, dict):
                continue
            url = str(src.get('url', '')).strip()
            if not _is_allowed_source_url(url):
                continue
            sources.append(ChatSource(
                title=src.get('title'),
                url=url,
                type=src.get('type'),
            ))

        # Validate suggested edges
        suggested_edges = []
        nodes_by_id = {n['id']: n for n in graph.get('nodes', [])}
        existing_edge_pairs, existing_edge_relationships = self._build_existing_edge_index(graph)
        seen_suggested_edges: set[Tuple[str, str, str]] = set()

        for edge in llm_output.get('suggested_edges', []):
            if not isinstance(edge, dict):
                continue

            source = edge.get('source')
            target = edge.get('target')
            relationship = edge.get('relationship', 'related')

            # Check 1: Both nodes must exist
            if source not in nodes_by_id or target not in nodes_by_id:
                LOGGER.warning(
                    f"LLM suggested edge with non-existent nodes: {source} -> {target}, "
                    f"filtering out"
                )
                continue
            
            # Check 2: Validate edge makes logical sense
            if not self._is_logical_edge(source, target, relationship, nodes_by_id):
                LOGGER.warning(
                    f"LLM suggested illogical edge: {source} -> {target} ({relationship}), "
                    f"filtering out"
                )
                continue

            normalized_source = str(source).strip().lower()
            normalized_target = str(target).strip().lower()
            normalized_relationship = self._normalize_relationship_name(relationship)

            # Check 3: Suppress already-existing relationships from current graph
            if (
                (normalized_source, normalized_target) in existing_edge_pairs
                or (normalized_source, normalized_target, normalized_relationship) in existing_edge_relationships
            ):
                LOGGER.info(
                    "Filtered duplicate edge suggestion already present in graph: %s -> %s (%s)",
                    source,
                    target,
                    relationship,
                )
                continue

            # Check 4: Suppress duplicate suggestions returned in the same response
            suggestion_key = (normalized_source, normalized_target, normalized_relationship)
            if suggestion_key in seen_suggested_edges:
                continue
            seen_suggested_edges.add(suggestion_key)

            suggested_edges.append(SuggestedEdge(
                source=source,
                target=target,
                relationship=relationship,
                confidence=min(1.0, max(0.0, edge.get('confidence', 0.5))),
                reason=edge.get('reason', ''),
            ))

        # Validate resources to highlight - prevent hallucinated resources
        resources_to_highlight = []
        for resource_id in llm_output.get('resources_to_highlight', []):
            if resource_id in nodes_by_id:
                resources_to_highlight.append(resource_id)
            else:
                LOGGER.warning(
                    f"LLM referenced non-existent resource: {resource_id}, filtering out"
                )

        # Validate criticality insights
        criticality_insights: List[CriticalityInsight] = []
        insights_raw = llm_output.get('criticality_insights', [])
        insights_list = insights_raw if isinstance(insights_raw, list) else [insights_raw]
        for insight in insights_list:
            if not isinstance(insight, dict):
                continue
            node_id = insight.get('node_id')
            if node_id in nodes_by_id:
                criticality_insights.append(CriticalityInsight(
                    node_id=node_id,
                    suggested_score=min(10, max(1, insight.get('suggested_score', 5))),
                    reason=insight.get('reason', ''),
                    current_score=self._node_criticality(nodes_by_id[node_id]),
                ))
            else:
                LOGGER.warning(f"LLM suggested criticality insight for non-existent node: {node_id}")

        recommendations_raw = llm_output.get('recommendations', [])
        if query_type == 'capabilities':
            recommendations = []
        elif flow == 'terraform':
            recommendations = recommendations_raw if isinstance(recommendations_raw, list) else []
        else:
            recommendations = self._normalize_recommendations(recommendations_raw, graph)

        raw_clarifying_questions = self._normalize_clarifying_questions(
            llm_output.get('clarifying_questions', [])
        )
        clarifying_questions = (
            self._normalize_terraform_clarifying_questions(raw_clarifying_questions)
            if flow == 'terraform'
            else raw_clarifying_questions
        )
        agent_owned_questions_removed = len(raw_clarifying_questions) > len(clarifying_questions)
        if flow == 'terraform':
            llm_output['clarifying_questions'] = clarifying_questions
        if query_type == 'capabilities':
            clarifying_questions = []
        force_proceed = self._is_force_proceed_query(query)
        clarification_limit_reached = (
            flow == 'terraform'
            and self._is_terraform_finalization_query(query)
            and bool(clarifying_questions)
        )
        if clarification_limit_reached:
            clarifying_questions = []
            llm_output['clarifying_questions'] = []
            llm_output['files'] = []
            llm_output.pop('terraform_code', None)
            llm_output.pop('terraform', None)
            llm_output.pop('code', None)
        block_terraform_until_clarified = (
            flow == 'terraform'
            and bool(clarifying_questions)
            and not force_proceed
        )

        # Extract terraform code and convert to string if needed
        terraform_code: Optional[str] = None
        terraform_validation: Optional[str] = None

        if not block_terraform_until_clarified:
            terraform_code = self._extract_terraform_code(llm_output)

            # Validate terraform code against failed findings
            terraform_validation = self._validate_terraform_against_findings(terraform_code, graph, llm_output)

            # One-shot repair loop for terraform outputs that fail validation.
            if flow == 'terraform' and terraform_code and terraform_validation:
                LOGGER.warning("Terraform output failed validation before repair: %s", terraform_validation)
                backend_retrieval_evidence = llm_output.get("_backend_retrieval_evidence")
                repaired_output = self._repair_terraform_output(
                    llm_output=llm_output,
                    terraform_validation=terraform_validation,
                    graph=graph,
                    agent_id=target_agent_id,
                    conversation_id=conversation_id,
                )
                if repaired_output:
                    llm_output = repaired_output
                    if isinstance(backend_retrieval_evidence, list):
                        llm_output["_backend_retrieval_evidence"] = backend_retrieval_evidence
                    recommendations_raw = llm_output.get('recommendations', [])
                    recommendations = recommendations_raw if isinstance(recommendations_raw, list) else []
                    terraform_code = self._extract_terraform_code(llm_output)
                    terraform_validation = self._validate_terraform_against_findings(terraform_code, graph, llm_output)
                    if terraform_validation:
                        LOGGER.warning("Terraform output failed validation after repair: %s", terraform_validation)

            if flow == 'terraform' and terraform_code and terraform_validation:
                terraform_code = None
                llm_output["files"] = []
                llm_output.pop("terraform_code", None)
                llm_output.pop("terraform", None)
                llm_output.pop("code", None)

        if clarification_limit_reached:
            terraform_validation = (
                "Terraform generation stopped after one clarification round because the supplied details "
                "were still insufficient. No configuration was generated or guessed. Start a new Terraform "
                "request with the requested existing HCL, resource names, regions, SKUs, and topology details."
            )
        elif agent_owned_questions_removed and not terraform_code:
            terraform_validation = (
                "Terraform generation stopped because the compiler failed to resolve an Azure SKU, redundancy, "
                "or regional capability decision that it owns. No user decision or guessed configuration is required."
            )

        compiler_blocker = None
        if flow == 'terraform' and not terraform_code and not clarifying_questions:
            compiler_blocker = self._extract_terraform_compiler_blocker(llm_output)
            if compiler_blocker and not terraform_validation:
                terraform_validation = compiler_blocker

        raw_message = llm_output.get('message')
        if clarification_limit_reached:
            message = "I could not safely generate Terraform from the supplied details."
        elif agent_owned_questions_removed and not terraform_code:
            message = "The compiler could not complete its Azure platform capability verification."
        elif compiler_blocker:
            message = "Terraform was not generated because the compiler returned no configuration files."
        elif isinstance(raw_message, str) and raw_message.strip():
            message = self._sanitize_response_message(raw_message)
        elif block_terraform_until_clarified:
            if self._is_clarification_answer_query(query):
                message = (
                    "I received your selections, but they do not include all concrete values needed for safe Terraform. "
                    "Please add the requested HCL, resource names, SKU/region, or topology details below."
                )
            else:
                message = "I need clarification before generating Terraform. Please answer the questions below."
        elif terraform_code:
            files_raw = llm_output.get('files')
            file_count = len(files_raw) if isinstance(files_raw, list) else 0
            message = (
                f"Generated Terraform output ({file_count} files)."
                if file_count > 0
                else "Generated Terraform output."
            )
        else:
            message = "The agent returned no usable output. Please retry the request."

        rag_trace: Optional[Dict[str, Any]] = None
        if include_rag_trace:
            source_type_counts: Dict[str, int] = {}
            for source in sources:
                source_type = (source.type or "unknown").strip() or "unknown"
                source_type_counts[source_type] = source_type_counts.get(source_type, 0) + 1

            external_types = {"APRL", "MicrosoftLearn"}
            external_source_types = sorted(
                source_type
                for source_type in source_type_counts
                if source_type in external_types
            )

            rag_expected = flow == 'resilience'
            rag_used = bool(external_source_types)

            rag_trace = {
                "trace_id": trace_id,
                "flow": flow,
                "agent_reference": target_agent_id,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "conversation_id": conversation_id,
                "rag_expected": rag_expected,
                "rag_used": rag_used,
                "source_type_counts": source_type_counts,
                "external_source_types": external_source_types,
                "external_sources_count": sum(source_type_counts.get(t, 0) for t in external_types),
                "clarifying_questions_count": len(clarifying_questions),
                "validation_passed": (rag_used if rag_expected else not rag_used),
            }

        metrics = None
        if isinstance(llm_metrics, dict) and llm_metrics:
            metrics = ChatMetrics(
                provider=llm_metrics.get('provider'),
                model=llm_metrics.get('model'),
                status=llm_metrics.get('status'),
                prompt_tokens=llm_metrics.get('prompt_tokens'),
                completion_tokens=llm_metrics.get('completion_tokens'),
                total_tokens=llm_metrics.get('total_tokens'),
                total_ms=llm_metrics.get('total_ms'),
                queue_ms=llm_metrics.get('queue_ms'),
                processing_ms=llm_metrics.get('processing_ms'),
            )

        return ChatResponse(
            message=message,
            sources=sources,
            metrics=metrics,
            suggested_edges=suggested_edges,
            resources_to_highlight=resources_to_highlight,
            criticality_insights=criticality_insights,
            recommendations=recommendations,
            remediation_guide=llm_output.get('remediation_guide'),
            terraform_code=terraform_code,
            terraform_validation=terraform_validation,
            clarifying_questions=clarifying_questions,
            agent_flow=flow,
            rag_trace=rag_trace,
            raw_llm_output=llm_output,
        )
