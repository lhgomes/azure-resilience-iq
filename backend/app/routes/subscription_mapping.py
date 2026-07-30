import json
import logging
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from azure.identity import DefaultAzureCredential
from azure.mgmt.subscription import SubscriptionClient
from azure.mgmt.resourcegraph.models import QueryRequest
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.collector.auth import get_arg_client
from app.config import get_subscription_dir
from app.llm.gateway import create_llm_gateway
from app.services.subscriptions import list_subscriptions
from app.settings import get_settings
from app.storage.conversation_store import (
    get_subscription_conversation_id,
    set_subscription_conversation_id,
)
from app.storage._json_repo import read_json, write_json, path_exists

router = APIRouter(prefix="/api/subscriptions", tags=["subscription-mapping"])
LOGGER = logging.getLogger(__name__)

STATUS_FILE_NAME = "subscription_mapping_status.json"


class SubscriptionMappingRequest(BaseModel):
    resource_groups: list[str] = Field(default_factory=list)
    tags: dict[str, str] = Field(default_factory=dict)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status_file(subscription_id: str) -> Path:
    return get_subscription_dir(subscription_id) / STATUS_FILE_NAME


def _normalize_subscription_id(value: str) -> str:
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid subscription id format")


def _write_status(subscription_id: str, payload: dict[str, Any]) -> None:
    write_json(_status_file(subscription_id), payload)


def _read_status(subscription_id: str) -> dict[str, Any] | None:
    status_file = _status_file(subscription_id)
    if not path_exists(status_file):
        return None
    payload = read_json(status_file, default=None)
    return payload if isinstance(payload, dict) else None


def _sanitize_filters(payload: SubscriptionMappingRequest) -> tuple[list[str], dict[str, str]]:
    resource_groups = [rg.strip() for rg in payload.resource_groups if rg and rg.strip()]
    tags = {
        str(k).strip(): str(v).strip()
        for k, v in payload.tags.items()
        if str(k).strip() and str(v).strip()
    }
    return resource_groups, tags


def _ensure_subscription_conversation_id(subscription_id: str) -> tuple[str, bool]:
    existing_conversation_id = get_subscription_conversation_id(subscription_id)
    if existing_conversation_id and str(existing_conversation_id).strip():
        return str(existing_conversation_id).strip(), False

    llm_gateway = create_llm_gateway(get_settings())
    if not llm_gateway.is_available():
        raise RuntimeError(
            "Cannot create conversation context: Foundry agent gateway is not available"
        )

    conversation_id = llm_gateway.create_conversation()
    if not conversation_id or not str(conversation_id).strip():
        raise RuntimeError("Conversation creation failed: empty conversation id")

    conversation_id = str(conversation_id).strip()
    set_subscription_conversation_id(subscription_id, conversation_id)
    return conversation_id, True


@router.get("/discover")
def discover_subscriptions() -> list[dict[str, Any]]:
    """Discover subscriptions from Azure account and flag already-mapped ones."""
    try:
        mapped_ids = {s["id"] for s in list_subscriptions()}

        try:
            credential = DefaultAzureCredential()
            client = SubscriptionClient(credential)

            discovered: list[dict[str, Any]] = []
            for subscription in client.subscriptions.list():
                subscription_id = str(subscription.subscription_id)
                display_name = str(subscription.display_name or subscription_id)
                state = str(getattr(subscription, "state", "Unknown") or "Unknown")

                discovered.append(
                    {
                        "id": subscription_id,
                        "name": display_name,
                        "state": state,
                        "mapped": subscription_id in mapped_ids,
                    }
                )
        except Exception as exc:
            LOGGER.warning("Azure subscription discovery failed: %s", exc)
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "AZURE_AUTH_REQUIRED",
                    "message": (
                        "Unable to discover Azure subscriptions. Please authenticate with Azure CLI "
                        "(run 'az login') or configure service principal environment variables."
                    ),
                },
            )

        discovered.sort(key=lambda item: (item["name"].lower(), item["id"]))
        return discovered
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "SUBSCRIPTION_DISCOVERY_FAILED",
                "message": f"Failed to discover subscriptions: {exc}",
            },
        )


@router.get("/{subscription_id}/resource-groups/discover")
def discover_resource_groups(subscription_id: str) -> list[str]:
    """Discover resource groups for a subscription from Azure Resource Graph."""
    try:
        subscription_id = _normalize_subscription_id(subscription_id)
        query = """
        Resources
        | where subscriptionId == '{subscription_id}'
        | where isnotempty(resourceGroup)
        | summarize by resourceGroup
        | project resourceGroup
        """.strip().format(subscription_id=subscription_id)

        client = get_arg_client()
        response = client.resources(
            QueryRequest(
                subscriptions=[subscription_id],
                query=query,
            )
        )

        groups = sorted(
            {
                str(row.get("resourceGroup", "")).strip()
                for row in response.data
                if str(row.get("resourceGroup", "")).strip()
            },
            key=lambda item: item.lower(),
        )
        return groups
    except Exception as exc:
        LOGGER.warning("Azure resource-group discovery failed for %s: %s", subscription_id, exc)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "AZURE_AUTH_REQUIRED",
                "message": (
                    "Unable to discover Azure resource groups. Please authenticate with Azure CLI "
                    "(run 'az login') or configure service principal environment variables."
                ),
            },
        )


@router.post("/{subscription_id}/map")
def start_subscription_mapping(subscription_id: str, request: SubscriptionMappingRequest):
    """
    Start asynchronous subscription mapping pipeline:
    collector -> resilience -> llm
    """
    try:
        subscription_id = _normalize_subscription_id(subscription_id)
        current = _read_status(subscription_id)
        if current and current.get("status") == "running":
            return JSONResponse(
                status_code=202,
                content=current,
                headers={"Location": f"/api/subscriptions/{subscription_id}/map/status"},
            )

        resource_groups, tags = _sanitize_filters(request)
        started_at = _utc_now_iso()

        stages: list[dict[str, Any]] = [
            {"name": "collector", "status": "pending"},
            {"name": "conversation", "status": "pending"},
            {"name": "llm", "status": "pending"},
            {"name": "resilience", "status": "pending"},
        ]

        status_payload: dict[str, Any] = {
            "subscription_id": subscription_id,
            "status": "running",
            "current_stage": "collector",
            "progress": 0,
            "message": "Starting mapping pipeline",
            "filters": {
                "resource_groups": resource_groups,
                "tags": tags,
            },
            "stages": stages,
            "started_at": started_at,
        }
        _write_status(subscription_id, status_payload)

        def set_stage(stage_name: str, **updates: Any) -> None:
            current_status = _read_status(subscription_id) or status_payload
            for stage in current_status.get("stages", []):
                if stage.get("name") == stage_name:
                    stage.update(updates)
                    break
            _write_status(subscription_id, current_status)

        def set_top_level(**updates: Any) -> None:
            current_status = _read_status(subscription_id) or status_payload
            current_status.update(updates)
            _write_status(subscription_id, current_status)

        def run_stage(stage_name: str, command: list[str], progress_before: int, progress_after: int) -> None:
            set_top_level(current_stage=stage_name, progress=progress_before, message=f"Running {stage_name}")
            set_stage(stage_name, status="running", started_at=_utc_now_iso())

            result = subprocess.run(command, capture_output=True, text=True)
            stage_output = {
                "returncode": result.returncode,
                "stdout_tail": (result.stdout or "")[-1200:],
                "stderr_tail": (result.stderr or "")[-1200:],
                "finished_at": _utc_now_iso(),
            }

            if result.returncode != 0:
                set_stage(stage_name, status="failed", **stage_output)
                raise RuntimeError(f"{stage_name} failed")

            set_stage(stage_name, status="completed", **stage_output)
            set_top_level(progress=progress_after, message=f"Completed {stage_name}")

        def worker() -> None:
            try:
                collector_cmd = [sys.executable, "-m", "app.collector.run", "--subscription-id", subscription_id]
                for rg in resource_groups:
                    collector_cmd.extend(["--resource-group", rg])
                for key, value in tags.items():
                    collector_cmd.extend(["--tag", f"{key}={value}"])

                resilience_cmd = [sys.executable, "-m", "app.resilience.run", "--subscription-id", subscription_id]
                llm_cmd = [sys.executable, "-m", "app.llm.run", "--subscription-id", subscription_id]

                run_stage("collector", collector_cmd, progress_before=5, progress_after=45)

                set_top_level(
                    current_stage="conversation",
                    progress=48,
                    message="Creating conversation context",
                )
                set_stage("conversation", status="running", started_at=_utc_now_iso())
                conversation_id, conversation_created = _ensure_subscription_conversation_id(subscription_id)
                set_stage(
                    "conversation",
                    status="completed",
                    finished_at=_utc_now_iso(),
                    conversation_id=conversation_id,
                    conversation_action="created" if conversation_created else "reused",
                )
                set_top_level(
                    progress=50,
                    message=(
                        "Conversation context created"
                        if conversation_created
                        else "Conversation context reused"
                    ),
                )

                run_stage("llm", llm_cmd, progress_before=55, progress_after=82)
                run_stage("resilience", resilience_cmd, progress_before=87, progress_after=100)

                set_top_level(
                    status="completed",
                    current_stage=None,
                    progress=100,
                    message="Subscription mapping completed",
                    finished_at=_utc_now_iso(),
                )
            except Exception as exc:
                current_status = _read_status(subscription_id) or status_payload
                current_status.update(
                    {
                        "status": "failed",
                        "message": str(exc),
                        "finished_at": _utc_now_iso(),
                    }
                )
                _write_status(subscription_id, current_status)

        threading.Thread(target=worker, daemon=True).start()

        return JSONResponse(
            status_code=202,
            content=status_payload,
            headers={"Location": f"/api/subscriptions/{subscription_id}/map/status"},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{subscription_id}/map/status")
def get_subscription_mapping_status(subscription_id: str):
    subscription_id = _normalize_subscription_id(subscription_id)
    status = _read_status(subscription_id)
    if not status:
        return {"subscription_id": subscription_id, "status": "idle", "progress": 0}
    return status
