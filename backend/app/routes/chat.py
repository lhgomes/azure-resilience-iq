"""
Chat API routes for infrastructure analysis.
Exposes LLM-powered chat capabilities for graph exploration and remediation guidance.
"""

from fastapi import APIRouter, HTTPException, Query, Body
from typing import Optional, List
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
            "reason": "Chat feature requires AZURE_OPENAI_EMBEDDING_DEPLOYMENT, AZURE_OPENAI_EMBEDDING_API_VERSION, and GUARDRAIL_SEMANTIC_THRESHOLD environment variables"
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
):
    """
    Process user message with LLM context.

    Request body:
    {
      "message": "What's failing on this resource?",
      "subscription_id": "...",
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

        # Get graph
        graph = get_workload_graph(subscription_id)
        if not graph:
            raise HTTPException(status_code=404, detail="Graph not found")

        LOGGER.info(f"Processing chat query for subscription {subscription_id}")
        LOGGER.debug(f"Query: {request.message[:100]}...")

        # Process with chat service
        chat_service = get_chat_service()
        response = await chat_service.process_query(
            query=request.message,
            graph=graph,
            subscription_id=subscription_id,
            context=request.context,
            conversation_history=request.conversation_history,
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
    is_healthy = chat_service.client is not None
    
    return {
        "status": "healthy" if is_healthy else "degraded",
        "chat_service": "available" if is_healthy else "unavailable",
        "subscription_id": subscription_id
    }
