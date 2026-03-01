"""
Terraform API endpoints.

Provides endpoints for uploading and processing Terraform files.
Generates resources.json and edges.json for analysis, bypassing the collector.
"""

import json
import logging
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from app.config import get_subscription_dir, get_resources_path, get_edges_path
from app.llm.gateway import create_llm_gateway
from app.settings import get_settings
from app.storage.conversation_store import (
    get_subscription_conversation_id,
    set_subscription_conversation_id,
)
from app.terraform.parser import TerraformParser
from app.terraform.generator import TerraformResourceGenerator

router = APIRouter(prefix="/api/terraform", tags=["terraform"])
LOGGER = logging.getLogger(__name__)
STATUS_FILE_NAME = "subscription_mapping_status.json"


class TerraformUploadResponse(BaseModel):
    """Response from Terraform upload."""
    subscription_id: str
    subscription_name: str
    resource_count: int
    edge_count: int
    message: str


class TerraformProcessRequest(BaseModel):
    """Request to process Terraform files."""
    subscription_id: Optional[str] = None
    subscription_name: str = "Terraform"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status_file(subscription_id: str) -> Path:
    return get_subscription_dir(subscription_id) / STATUS_FILE_NAME


def _write_status(subscription_id: str, payload: dict[str, Any]) -> None:
    sub_dir = get_subscription_dir(subscription_id)
    sub_dir.mkdir(parents=True, exist_ok=True)
    _status_file(subscription_id).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_status(subscription_id: str) -> dict[str, Any] | None:
    status_file = _status_file(subscription_id)
    if not status_file.exists():
        return None
    try:
        return json.loads(status_file.read_text(encoding="utf-8"))
    except Exception:
        return None


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


@router.post("/upload")
async def upload_terraform_files(
    files: list[UploadFile] = File(...),
    subscription_id: Optional[str] = Form(None),
    subscription_name: str = Form("Terraform"),
) -> TerraformUploadResponse:
    """
    Upload Terraform files and generate resources/edges.
    
    Accepts multiple .tf or .json files, parses them, and generates
    the standard resources.json and edges.json format.
    
    Args:
        files: List of Terraform .tf or .json files
        subscription_id: Optional UUID (auto-generated if not provided)
        subscription_name: Friendly name for the subscription
        
    Returns:
        TerraformUploadResponse with subscription info and counts
        
    Example:
        curl -X POST "http://localhost:8000/api/terraform/upload" \\
          -F "files=@main.tf" \\
          -F "files=@variables.tf" \\
          -F "subscription_name=My Terraform"
    """
    
    # Generate subscription ID if not provided
    if not subscription_id:
        subscription_id = str(uuid.uuid4())
    
    # Validate subscription ID format
    try:
        uuid.UUID(subscription_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid subscription_id format. Must be a valid UUID."
        )
    
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    
    # Persist uploaded files into the same fake subscription folder used for data outputs
    sub_dir = get_subscription_dir(subscription_id)
    sub_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir = sub_dir / "terraform_uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    current_status = _read_status(subscription_id)
    if current_status and current_status.get("status") == "running":
        raise HTTPException(
            status_code=409,
            detail="A mapping pipeline is already running for this subscription",
        )

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
        "message": "Starting Terraform mapping pipeline",
        "filters": {
            "resource_groups": [],
            "tags": {},
        },
        "stages": stages,
        "started_at": _utc_now_iso(),
    }
    _write_status(subscription_id, status_payload)

    def set_stage(stage_name: str, **updates: Any) -> None:
        current = _read_status(subscription_id) or status_payload
        for stage in current.get("stages", []):
            if stage.get("name") == stage_name:
                stage.update(updates)
                break
        _write_status(subscription_id, current)

    def set_top_level(**updates: Any) -> None:
        current = _read_status(subscription_id) or status_payload
        current.update(updates)
        _write_status(subscription_id, current)

    # Save uploaded files
    saved_files = []
    for file in files:
        if not file.filename:
            continue

        original_name = Path(file.filename).name
        if not original_name:
            continue

        target_path = uploads_dir / original_name
        if target_path.exists():
            stem = Path(original_name).stem
            suffix = Path(original_name).suffix
            counter = 1
            while target_path.exists():
                target_path = uploads_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        content = await file.read()
        target_path.write_bytes(content)
        saved_files.append(target_path)
        
    if not saved_files:
        set_top_level(status="failed", message="No valid files uploaded", finished_at=_utc_now_iso())
        raise HTTPException(status_code=400, detail="No valid files uploaded")
        
    # Run Terraform collector stage synchronously so caller gets immediate counts
    set_top_level(current_stage="collector", progress=5, message="Running Terraform collector")
    set_stage("collector", status="running", started_at=_utc_now_iso())
    try:
        parser = TerraformParser()
        all_resources = []

        for file_path in saved_files:
            if file_path.suffix == ".json":
                try:
                    json_data = json.loads(file_path.read_text())
                    resources = parser.parse_json_state(json_data)
                except json.JSONDecodeError as e:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid JSON in {file_path.name}: {str(e)}"
                    )
            else:
                resources = parser.parse_file(file_path)

            all_resources.extend(resources)

        if not all_resources:
            raise HTTPException(
                status_code=400,
                detail="No Azure resources found in uploaded files"
            )

        generator = TerraformResourceGenerator(subscription_id, subscription_name)
        generator.add_resources(all_resources)
        resources_output, edges_output = generator.generate()

        resources_path = get_resources_path(subscription_id)
        resources_path.write_text(json.dumps(resources_output, indent=2))

        edges_path = get_edges_path(subscription_id)
        edges_path.write_text(json.dumps(edges_output, indent=2))

        set_stage(
            "collector",
            status="completed",
            finished_at=_utc_now_iso(),
            stdout_tail=(
                f"Terraform collector generated {len(resources_output['resources'])} resources and "
                f"{len(edges_output['edges'])} edges"
            ),
            returncode=0,
        )
        set_top_level(progress=45, message="Terraform collector completed")
    except HTTPException as exc:
        set_stage(
            "collector",
            status="failed",
            finished_at=_utc_now_iso(),
            stderr_tail=str(exc.detail),
            returncode=1,
        )
        set_top_level(status="failed", message=str(exc.detail), finished_at=_utc_now_iso())
        raise
    except Exception as exc:
        set_stage(
            "collector",
            status="failed",
            finished_at=_utc_now_iso(),
            stderr_tail=str(exc),
            returncode=1,
        )
        set_top_level(status="failed", message=str(exc), finished_at=_utc_now_iso())
        raise HTTPException(status_code=500, detail=str(exc))

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

    def post_collector_worker() -> None:
        try:
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

            llm_cmd = [sys.executable, "-m", "app.llm.run", "--subscription-id", subscription_id]
            resilience_cmd = [
                sys.executable,
                "-m",
                "app.resilience.run",
                "--subscription-id",
                subscription_id,
            ]

            run_stage("llm", llm_cmd, progress_before=55, progress_after=82)
            run_stage("resilience", resilience_cmd, progress_before=87, progress_after=100)

            set_top_level(
                status="completed",
                current_stage=None,
                progress=100,
                message="Terraform mapping completed",
                finished_at=_utc_now_iso(),
            )
        except Exception as exc:
            LOGGER.exception("Terraform post-collector pipeline failed for %s", subscription_id)
            failed_status = _read_status(subscription_id) or status_payload
            failed_status.update(
                {
                    "status": "failed",
                    "message": str(exc),
                    "finished_at": _utc_now_iso(),
                }
            )
            _write_status(subscription_id, failed_status)

    threading.Thread(target=post_collector_worker, daemon=True).start()

    return TerraformUploadResponse(
        subscription_id=subscription_id,
        subscription_name=subscription_name,
        resource_count=len(resources_output["resources"]),
        edge_count=len(edges_output["edges"]),
        message=(
            f"Successfully processed {len(saved_files)} files "
            f"(saved in {uploads_dir.name})"
        )
    )


@router.get("/subscriptions")
async def list_terraform_subscriptions() -> list[dict]:
    """
    List all subscriptions that were created from Terraform uploads.
    
    Returns:
        List of subscription info dicts with id, name, resource_count
    """
    from app.config import DATA_DIR
    import re
    
    subscriptions = []
    uuid_pattern = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.IGNORECASE
    )
    
    if not DATA_DIR.exists():
        return subscriptions
    
    for item in DATA_DIR.iterdir():
        if not item.is_dir():
            continue
        
        # Check if directory name is a valid UUID
        if not uuid_pattern.match(item.name):
            continue
        
        subscription_id = item.name
        resources_path = item / "resources.json"
        
        if not resources_path.exists():
            continue
        
        try:
            with resources_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                subscriptions.append({
                    "id": subscription_id,
                    "name": data.get("subscription_name", subscription_id),
                    "resource_count": len(data.get("resources", [])),
                })
        except Exception:
            continue
    
    return subscriptions


@router.post("/health")
async def terraform_health() -> dict:
    """
    Health check for Terraform module.
    
    Returns:
        Status and version information
    """
    return {
        "status": "healthy",
        "module": "terraform",
        "capabilities": [
            "parse_hcl",
            "parse_json_state",
            "generate_resources",
            "generate_edges",
            "create_subscriptions",
        ]
    }
