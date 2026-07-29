"""Tests for the Service Group read-access probe.

The probe underpins the UI gate that disables the Service Group import
affordance when the backend identity cannot read Service Groups. It must mirror
the *actual* read path — an Azure Resource Graph query — rather than a direct
ARM GET on the tenant-root Service Group (which requires a separate
tenant-admin grant most callers lack). These tests pin that behaviour.
"""

from __future__ import annotations

import pytest

from app.servicegroups import applier


def test_returns_available_when_arg_query_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(applier, "list_service_groups", lambda: [{"name": "sg1"}])

    access = applier.check_service_group_read_access()

    assert access.available is True
    assert access.reason is None


def test_returns_available_when_arg_query_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    # An empty result means "no Service Groups visible", not "no access" — the
    # feature works, so the gate must stay open.
    monkeypatch.setattr(applier, "list_service_groups", lambda: [])

    access = applier.check_service_group_read_access()

    assert access.available is True
    assert access.reason is None


def test_returns_unavailable_when_arg_query_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> list:
        raise RuntimeError("no credential")

    monkeypatch.setattr(applier, "list_service_groups", _boom)

    access = applier.check_service_group_read_access()

    assert access.available is False
    assert "Azure Resource Graph" in (access.reason or "")
    assert "no credential" in (access.reason or "")
