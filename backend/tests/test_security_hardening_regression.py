from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import resilience as resilience_routes
from app.routes import terraform as terraform_routes
from app.storage import _json_repo
from app.terraform.parser import TerraformParser


def test_json_repo_read_json_returns_default_for_outside_data_dir(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text('{"x": 1}', encoding="utf-8")

    result = _json_repo.read_json(outside, default={"default": True})
    assert result == {"default": True}


def test_json_repo_write_json_rejects_outside_data_dir(tmp_path: Path) -> None:
    outside = tmp_path / "outside-write.json"

    with pytest.raises(ValueError, match="Path must be inside data directory"):
        _json_repo.write_json(outside, {"x": 1})


def test_terraform_normalize_subscription_id_accepts_uuid() -> None:
    value = terraform_routes._normalize_subscription_id("123e4567-e89b-12d3-a456-426614174000")
    assert value == "123e4567-e89b-12d3-a456-426614174000"


def test_terraform_normalize_subscription_id_rejects_invalid() -> None:
    with pytest.raises(Exception):
        terraform_routes._normalize_subscription_id("not-a-uuid")


def test_ensure_within_dir_accepts_child_path(tmp_path: Path) -> None:
    base = tmp_path / "uploads"
    base.mkdir(parents=True)
    candidate = base / "main.tf"

    resolved = terraform_routes._ensure_within_dir(base, candidate)
    assert resolved == candidate.resolve()


def test_ensure_within_dir_rejects_escape_path(tmp_path: Path) -> None:
    base = tmp_path / "uploads"
    base.mkdir(parents=True)
    escaped = base.parent / "evil.tf"

    with pytest.raises(Exception):
        terraform_routes._ensure_within_dir(base, escaped)


def test_terraform_parser_rejects_file_outside_trusted_root(tmp_path: Path) -> None:
    trusted_root = tmp_path / "trusted"
    trusted_root.mkdir(parents=True)

    outside_file = tmp_path / "outside.tf"
    outside_file.write_text('resource "azurerm_resource_group" "rg" {}', encoding="utf-8")

    parser = TerraformParser()
    with pytest.raises(ValueError, match="outside trusted root"):
        parser.parse_file(outside_file, trusted_root=trusted_root)


def test_resilience_health_hides_internal_exception_details(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom():
        raise RuntimeError("sensitive backend failure")

    monkeypatch.setattr(resilience_routes, "get_aprl_catalog", _boom)

    app = FastAPI()
    app.include_router(resilience_routes.router)
    client = TestClient(app)

    response = client.get("/api/resilience/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "unhealthy"
    assert payload["error"] == "Internal server error"
    assert "sensitive backend failure" not in response.text
