"""
Chat API routes for infrastructure analysis.
Exposes LLM-powered chat capabilities for graph exploration and remediation guidance.
"""

from fastapi import APIRouter, HTTPException, Query, Body
from typing import Optional, List, Dict, Any
import logging

from app.chat.models import ChatRequest, ChatResponse
from app.chat.service import ChatService
from app.services.workloads import get_workload_graph
from app.logger import get_logger
from app.settings import get_settings

LOGGER = get_logger(__name__)

router = APIRouter(
    prefix="/api/subscriptions",
    tags=["chat"]
)

# Global chat availability endpoint
chat_router = APIRouter(
    prefix="/api/chat",
    tags=["chat"]
)

# Initialize chat service (singleton)
_chat_service = None


def _dedupe_by_key(items: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    output: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_key = item.get(key)
        if not item_key:
            continue
        item_key_str = str(item_key)
        if item_key_str in seen:
            continue
        seen.add(item_key_str)
        output.append(item)
    return output


def _merge_graph_payloads(payloads: List[Dict[str, Any]]) -> Dict[str, Any]:
    merged_nodes: List[Dict[str, Any]] = []
    merged_edges: List[Dict[str, Any]] = []
    merged_llm_nodes: List[Dict[str, Any]] = []
    merged_llm_edges: List[Dict[str, Any]] = []
    merged_node_overrides: Dict[str, Any] = {}
    merged_edge_overrides: Dict[str, Any] = {}
    merged_resilience_evals: Dict[str, Any] = {}
    merged_resilience_overrides: Dict[str, Any] = {}

    for payload in payloads:
        merged_nodes.extend(payload.get("nodes", []) or [])
        merged_edges.extend(payload.get("edges", []) or [])
        merged_llm_nodes.extend((payload.get("llm_annotations") or {}).get("nodes", []) or [])
        merged_llm_edges.extend((payload.get("llm_annotations") or {}).get("edges", []) or [])
        merged_node_overrides.update(payload.get("node_overrides") or {})
        merged_edge_overrides.update(payload.get("edge_overrides") or {})
        merged_resilience_evals.update((payload.get("resilience_evaluations") or {}).get("evaluations", {}) or {})
        merged_resilience_overrides.update(payload.get("resilience_overrides") or {})

    llm_edge_seen: set[str] = set()
    deduped_llm_edges: List[Dict[str, Any]] = []
    for edge in merged_llm_edges:
        if not isinstance(edge, dict):
            continue
        dedupe_key = "|".join(
            [
                str(edge.get("source") or ""),
                str(edge.get("target") or ""),
                str(edge.get("relationship") or ""),
            ]
        )
        if dedupe_key in llm_edge_seen:
            continue
        llm_edge_seen.add(dedupe_key)
        deduped_llm_edges.append(edge)

    return {
        "nodes": _dedupe_by_key(merged_nodes, "id"),
        "edges": _dedupe_by_key(merged_edges, "id"),
        "llm_annotations": {
            "nodes": _dedupe_by_key(merged_llm_nodes, "node_id"),
            "edges": deduped_llm_edges,
        },
        "node_overrides": merged_node_overrides,
        "edge_overrides": merged_edge_overrides,
        "resilience_evaluations": {"evaluations": merged_resilience_evals},
        "resilience_overrides": merged_resilience_overrides,
    }


@chat_router.get("/availability")
async def chat_availability():
    """
    Check if chat feature is available.
    
    Returns:
    {
      "available": true/false,
      "reason": "string (if not available)"
    }
    """
    settings = get_settings()
    is_available = settings.is_chat_available()
    
    if is_available:
        return {
            "available": True,
            "reason": None
        }
    else:
        return {
            "available": False,
            "reason": "Chat feature requires APIM + Foundry configuration: ai_agent.gateway_base_url, flow-specific agent references (chat/resilience/annotations), and AI_GATEWAY_SUBSCRIPTION_KEY"
        }


def get_chat_service() -> ChatService:
    """Get or create chat service instance."""
    global _chat_service
    if _chat_service is None:
        _chat_service = ChatService()
    return _chat_service


@router.post("/{subscription_id}/chat")
async def chat_message(
    subscription_id: str,
    request: ChatRequest,
    include_rag_trace: bool = Query(False, description="Include debug rag_trace diagnostics in response"),
):
    """
    Process user message with LLM context.

    Request body:
    {
      "message": "What's failing on this resource?",
      "subscription_id": "...",
            "referenced_resource_ids": ["/subscriptions/.../resourceGroups/.../providers/..."],
      "context": {
        "selected_resource_id": "...",
        "tab": "findings"
      },
      "conversation_history": [
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."}
      ]
    }

    Returns:
    {
      "message": "...",
      "suggested_edges": [...],
      "resources_to_highlight": [...],
      "clarifying_questions": [...],
      "remediation_guide": {...},
      "recommendations": [...]
    }
    """
    try:
        # Verify subscription matches request
        if request.subscription_id != subscription_id:
            raise HTTPException(
                status_code=400,
                detail="Subscription ID mismatch"
            )

        selected_subscriptions: List[str] = []
        if isinstance(request.context, dict):
            raw_selected = request.context.get("selected_subscriptions")
            if isinstance(raw_selected, list):
                selected_subscriptions = [str(item).strip() for item in raw_selected if str(item).strip()]

        if len(selected_subscriptions) >= 2:
            payloads: List[Dict[str, Any]] = []
            for selected_subscription_id in selected_subscriptions:
                graph_payload = get_workload_graph(selected_subscription_id)
                if graph_payload:
                    payloads.append(graph_payload)
            if not payloads:
                raise HTTPException(status_code=404, detail="Graph not found")
            graph = _merge_graph_payloads(payloads)
        else:
            graph = get_workload_graph(subscription_id)
            if not graph:
                raise HTTPException(status_code=404, detail="Graph not found")

        LOGGER.info(
            "Processing chat query for subscription %s (selected_subscriptions=%s)",
            subscription_id,
            selected_subscriptions,
        )
        LOGGER.debug(f"Query: {request.message[:100]}...")

        # Process with chat service
        chat_service = get_chat_service()
        response = await chat_service.process_query(
            query=request.message,
            graph=graph,
            subscription_id=subscription_id,
            context=request.context,
            conversation_history=request.conversation_history,
            referenced_resource_ids=request.referenced_resource_ids,
            include_rag_trace=include_rag_trace,
        )

        LOGGER.debug(f"Chat response generated successfully")
        return response

    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{subscription_id}/chat/baseline")
async def chat_baseline(subscription_id: str):
    """Return baseline summary from the latest LLM annotations."""
    try:
        graph = get_workload_graph(subscription_id)
        if not graph:
            raise HTTPException(status_code=404, detail="Graph not found")

        chat_service = get_chat_service()
        baseline = chat_service.get_baseline(graph)
        return {
            "subscription_id": subscription_id,
            "summary": baseline.get("summary"),
            "references": baseline.get("references", []),
            "reference_count": baseline.get("reference_count", 0),
        }
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.error(f"Chat baseline error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{subscription_id}/chat/action")
async def apply_chat_action(
    subscription_id: str,
    action_type: str = Body(..., description="Type of action: add_edge, update_criticality, etc"),
    payload: dict = Body(..., description="Action payload")
):
    """
    Apply suggested action from chat (e.g., add edge, update criticality).
    
    This endpoint delegates to existing mutation endpoints.
    
    Examples:
    - action_type=add_edge, payload={source, target, relationship, confidence}
    - action_type=update_criticality, payload={node_id, score}
    """
    try:
        if action_type == 'add_edge':
            # Will delegate to POST /api/subscriptions/{id}/edges
            LOGGER.info(f"Chat action: add edge {payload.get('source')} -> {payload.get('target')}")
            return {
                "status": "success",
                "message": "Edge will be added via main API",
                "next_endpoint": f"/api/subscriptions/{subscription_id}/edges"
            }
        
        elif action_type == 'update_criticality':
            # Will delegate to PATCH /api/subscriptions/{id}/nodes/{node_id}/criticality
            LOGGER.info(f"Chat action: update criticality for {payload.get('node_id')}")
            return {
                "status": "success",
                "message": "Criticality will be updated via main API",
                "next_endpoint": f"/api/subscriptions/{subscription_id}/nodes/{payload.get('node_id')}/criticality"
            }
        
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown action type: {action_type}"
            )
    
    except Exception as e:
        LOGGER.error(f"Chat action error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{subscription_id}/chat/health")
async def chat_health(subscription_id: str):
    """Health check for chat service."""
    chat_service = get_chat_service()
    llm_gateway = getattr(chat_service, "llm_gateway", None)
    is_healthy = bool(llm_gateway and llm_gateway.is_available())
    
    return {
        "status": "healthy" if is_healthy else "degraded",
        "chat_service": "available" if is_healthy else "unavailable",
        "subscription_id": subscription_id
    }
