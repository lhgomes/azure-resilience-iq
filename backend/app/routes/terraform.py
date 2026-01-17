"""
Terraform API endpoints.

Provides endpoints for uploading and processing Terraform files.
Generates resources.json and edges.json for analysis, bypassing the collector.
"""

import json
import uuid
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from app.config import get_subscription_dir, get_resources_path, get_edges_path
from app.terraform.parser import TerraformParser
from app.terraform.generator import TerraformResourceGenerator

router = APIRouter(prefix="/api/terraform", tags=["terraform"])


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
    
    # Create temporary directory for uploads
    temp_dir = Path(f"/tmp/terraform_upload_{uuid.uuid4()}")
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Save uploaded files
        saved_files = []
        for file in files:
            if not file.filename:
                continue
            
            file_path = temp_dir / file.filename
            content = await file.read()
            file_path.write_bytes(content)
            saved_files.append(file_path)
        
        if not saved_files:
            raise HTTPException(status_code=400, detail="No valid files uploaded")
        
        # Parse Terraform files
        parser = TerraformParser()
        all_resources = []
        
        for file_path in saved_files:
            if file_path.suffix == ".json":
                # Parse as JSON state
                try:
                    json_data = json.loads(file_path.read_text())
                    resources = parser.parse_json_state(json_data)
                except json.JSONDecodeError as e:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid JSON in {file_path.name}: {str(e)}"
                    )
            else:
                # Parse as HCL
                resources = parser.parse_file(file_path)
            
            all_resources.extend(resources)
        
        if not all_resources:
            raise HTTPException(
                status_code=400,
                detail="No Azure resources found in uploaded files"
            )
        
        # Generate resources and edges
        generator = TerraformResourceGenerator(subscription_id, subscription_name)
        generator.add_resources(all_resources)
        resources_output, edges_output = generator.generate()
        
        # Save to subscription directory
        sub_dir = get_subscription_dir(subscription_id)
        sub_dir.mkdir(parents=True, exist_ok=True)
        
        resources_path = get_resources_path(subscription_id)
        resources_path.write_text(json.dumps(resources_output, indent=2))
        
        edges_path = get_edges_path(subscription_id)
        edges_path.write_text(json.dumps(edges_output, indent=2))
        
        return TerraformUploadResponse(
            subscription_id=subscription_id,
            subscription_name=subscription_name,
            resource_count=len(resources_output["resources"]),
            edge_count=len(edges_output["edges"]),
            message=f"Successfully processed {len(saved_files)} files"
        )
    
    finally:
        # Cleanup
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


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
