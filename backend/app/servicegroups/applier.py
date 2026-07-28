from __future__ import annotations

import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import httpx
from azure.identity import DefaultAzureCredential

from .models import (
    ServiceGroupMemberRef,
    SERVICE_GROUP_API_VERSION,
    SERVICE_GROUP_MEMBER_API_VERSION,
)
from .reader import list_service_group_member_relationships
from app.relationships.utils import norm_id

ARM_BASE = "https://management.azure.com"
ARM_SCOPE = "https://management.azure.com/.default"
_HTTP_TIMEOUT = 30.0

# Writing a serviceGroupMember is a *linked action*: ARM authorizes it against
# both the member resource scope AND the target Service Group scope. Right after
# a Service Group is created, the creator's RBAC on that new tenant-level scope
# propagates asynchronously, so member writes issued too soon fail with this
# code (returned as HTTP 403). It is transient — not a real permission gap — so
# it is retried with backoff instead of being reported as permission_denied.
_LINKED_AUTH_FAILED = "LinkedAuthorizationFailed"
_MEMBER_WRITE_MAX_ATTEMPTS = 6
_MEMBER_WRITE_BACKOFF_SECONDS = 2.0

# Member writes are independent, I/O-bound ARM calls, so they are issued
# concurrently through a bounded pool (httpx.Client is thread-safe; the token is
# a plain string). This also collapses the LinkedAuthorizationFailed propagation
# wait: instead of each member serially exhausting its retries, all pending
# members back off and retry together, so the whole batch clears in roughly one
# member's retry cycle. The cap stays well under ARM's subscription write bucket.
_MEMBER_WRITE_MAX_CONCURRENCY = 8


@dataclass
class ApplyOutcome:
    applied: List[str] = field(default_factory=list)
    detached: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    permission_denied: bool = False
    error_message: Optional[str] = None


def _service_group_id(service_group_name: str) -> str:
    return f"/providers/Microsoft.Management/serviceGroups/{service_group_name}"


def _tenant_id_from_token(token: str) -> Optional[str]:
    """Read the tenant id (``tid``) from an ARM access token's JWT payload.

    The root Service Group — used as the default ``parent`` of a new top-level
    Service Group — has an id equal to the tenant id.
    """
    try:
        payload_segment = token.split(".")[1]
        padding = "=" * (-len(payload_segment) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_segment + padding))
        tenant_id = claims.get("tid")
        return str(tenant_id) if tenant_id else None
    except Exception:
        return None


def _existing_parent_id(response: httpx.Response) -> Optional[str]:
    """Return the parent Service Group id from an existing SG GET response.

    Preserving the current parent keeps a Workload save from re-parenting a
    Service Group that already lives somewhere in the hierarchy.
    """
    if not response.is_success:
        return None
    try:
        props = (response.json() or {}).get("properties") or {}
        parent = props.get("parent") or {}
        parent_id = parent.get("resourceId")
        return str(parent_id) if parent_id else None
    except Exception:
        return None


def _extract_error(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
    except Exception:
        pass
    return (resp.text or f"HTTP {resp.status_code}")[:500]


def _error_code(resp: httpx.Response) -> Optional[str]:
    try:
        payload = resp.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict) and error.get("code"):
            return str(error["code"])
    except Exception:
        pass
    return None


def _put(client: httpx.Client, token: str, path: str, api_version: str, body: dict) -> httpx.Response:
    return client.put(
        f"{ARM_BASE}{path}",
        params={"api-version": api_version},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=_HTTP_TIMEOUT,
    )


def _put_member_with_retry(
    client: httpx.Client, token: str, member_path: str, body: dict
) -> httpx.Response:
    """PUT a serviceGroupMember relationship, retrying the transient
    ``LinkedAuthorizationFailed`` 403 that occurs while a freshly-created
    Service Group's RBAC propagates to its tenant-level scope. Any other
    response (success, a genuine 403, or another error) is returned as-is.
    """
    resp = _put(client, token, member_path, SERVICE_GROUP_MEMBER_API_VERSION, body)
    attempt = 1
    while (
        resp.status_code == 403
        and _error_code(resp) == _LINKED_AUTH_FAILED
        and attempt < _MEMBER_WRITE_MAX_ATTEMPTS
    ):
        time.sleep(_MEMBER_WRITE_BACKOFF_SECONDS * attempt)
        resp = _put(client, token, member_path, SERVICE_GROUP_MEMBER_API_VERSION, body)
        attempt += 1
    return resp


def _get(client: httpx.Client, token: str, path: str, api_version: str) -> httpx.Response:
    return client.get(
        f"{ARM_BASE}{path}",
        params={"api-version": api_version},
        headers={"Authorization": f"Bearer {token}"},
        timeout=_HTTP_TIMEOUT,
    )


def _delete(client: httpx.Client, token: str, path: str, api_version: str) -> httpx.Response:
    return client.delete(
        f"{ARM_BASE}{path}",
        params={"api-version": api_version},
        headers={"Authorization": f"Bearer {token}"},
        timeout=_HTTP_TIMEOUT,
    )


def _acquire_token() -> str:
    credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
    return credential.get_token(ARM_SCOPE).token


def apply_service_group(
    service_group_name: str,
    display_name: str,
    members: List[ServiceGroupMemberRef],
    detach_resource_ids: Optional[List[str]] = None,
    parent_service_group_id: Optional[str] = None,
    prune_to_members: bool = False,
) -> ApplyOutcome:
    """
    Create/update the Service Group and attach each member using the backend
    identity (managed identity on the VM, az-cli locally).

    Detach behaviour:
      - ``prune_to_members=True`` -> full sync: every CURRENT member (discovered
        from Azure Resource Graph) that is not in ``members`` is removed. The
        desired member list is the single source of truth for the Service Group.
      - otherwise -> only the resources in ``detach_resource_ids`` are removed.

    A 403 anywhere is reported as ``permission_denied`` so the caller can fall
    back to exporting the IaC artifact for a team with the required rights.
    """
    all_member_ids = [m.resource_id for m in members]

    try:
        token = _acquire_token()
    except Exception as exc:  # credential/token acquisition failure
        return ApplyOutcome(failed=all_member_ids, error_message=f"Failed to acquire Azure token: {exc}")

    sg_path = _service_group_id(service_group_name)

    with httpx.Client() as client:
        # Resolve the parent Service Group:
        #   - existing SG -> preserve its current parent (a save must not re-parent it)
        #   - new SG      -> caller-selected parent, else default to the tenant root
        try:
            existing = _get(client, token, sg_path, SERVICE_GROUP_API_VERSION)
        except httpx.HTTPError as exc:
            return ApplyOutcome(failed=all_member_ids, error_message=f"Service Group lookup failed: {exc}")

        if existing.status_code == 403:
            return ApplyOutcome(
                failed=all_member_ids,
                permission_denied=True,
                error_message=_extract_error(existing),
            )

        effective_parent = _existing_parent_id(existing) or parent_service_group_id
        if not effective_parent:
            tenant_id = _tenant_id_from_token(token)
            if not tenant_id:
                return ApplyOutcome(
                    failed=all_member_ids,
                    error_message="Unable to determine the tenant id from the Azure token, which "
                    "is required to default the Service Group parent to the tenant root.",
                )
            effective_parent = _service_group_id(tenant_id)

        # 1) Create/update the Service Group under the resolved parent.
        try:
            sg_resp = _put(
                client,
                token,
                sg_path,
                SERVICE_GROUP_API_VERSION,
                {
                    "properties": {
                        "displayName": display_name,
                        "parent": {"resourceId": effective_parent},
                    }
                },
            )
        except httpx.HTTPError as exc:
            return ApplyOutcome(failed=all_member_ids, error_message=f"Service Group request failed: {exc}")

        if sg_resp.status_code == 403:
            return ApplyOutcome(
                failed=all_member_ids,
                permission_denied=True,
                error_message=_extract_error(sg_resp),
            )
        if not sg_resp.is_success:
            return ApplyOutcome(failed=all_member_ids, error_message=_extract_error(sg_resp))

        # Discover the SG's CURRENT member relationships (whatever named them:
        # this tool, IaC, or the portal). This lets us skip resources that are
        # already members and detach dropped ones by their real relationship id,
        # instead of assuming a relationship name — which created duplicates and
        # left stale members behind.
        try:
            existing_by_source: dict[str, List[dict]] = {}
            for rel in list_service_group_member_relationships(service_group_name):
                existing_by_source.setdefault(rel["source_id"], []).append(rel)
        except Exception as exc:
            # Membership discovery uses the same Azure Resource Graph read the
            # import relies on. Without it we cannot safely attach (we would
            # duplicate resources already added under an IaC/portal relationship
            # name) or detach (we cannot resolve the real relationship name).
            # Fail closed so the caller falls back to the IaC artifact instead of
            # corrupting the Service Group membership.
            return ApplyOutcome(
                failed=all_member_ids,
                error_message=(
                    "Unable to read the Service Group's current members from Azure "
                    "Resource Graph, which is required to sync membership safely; no "
                    f"membership changes were applied ({exc})."
                ),
            )

        # 2) Attach each desired member, skipping any that are already members.
        #    Pending writes go out concurrently (bounded pool) since each member
        #    PUT is an independent ARM call.
        outcome = ApplyOutcome()

        to_attach: List[ServiceGroupMemberRef] = []
        for member in members:
            if norm_id(member.resource_id) in existing_by_source:
                # Already a member (possibly under an IaC/portal name) — do not
                # create a second relationship for the same resource.
                outcome.applied.append(member.resource_id)
            else:
                to_attach.append(member)

        def _attach(
            member: ServiceGroupMemberRef,
        ) -> Tuple[ServiceGroupMemberRef, Optional[httpx.Response], Optional[Exception]]:
            member_path = (
                f"{member.resource_id}"
                f"/providers/Microsoft.Relationships/serviceGroupMember/{member.member_name}"
            )
            try:
                resp = _put_member_with_retry(
                    client, token, member_path, {"properties": {"targetId": sg_path}}
                )
                return member, resp, None
            except httpx.HTTPError as exc:
                return member, None, exc

        if to_attach:
            with ThreadPoolExecutor(
                max_workers=min(_MEMBER_WRITE_MAX_CONCURRENCY, len(to_attach))
            ) as executor:
                results = list(executor.map(_attach, to_attach))
        else:
            results = []

        for member, member_resp, exc in results:
            if exc is not None:
                outcome.failed.append(member.resource_id)
                outcome.error_message = f"Member request failed: {exc}"
                continue

            if member_resp.is_success:
                outcome.applied.append(member.resource_id)
            else:
                outcome.failed.append(member.resource_id)
                code = _error_code(member_resp)
                if member_resp.status_code == 403 and code != _LINKED_AUTH_FAILED:
                    # A genuine authorization gap on the member or Service Group
                    # scope — surface it so the caller can fall back to the IaC
                    # artifact for a team with the required rights.
                    outcome.permission_denied = True
                    outcome.error_message = _extract_error(member_resp)
                elif code == _LINKED_AUTH_FAILED:
                    # Still propagating after retries — transient, not a real
                    # permission gap. Ask the user to save again shortly.
                    outcome.error_message = (
                        "The Service Group was created, but its permissions had not "
                        "finished propagating to every member yet (Azure replicates "
                        "the new tenant-scoped Service Group asynchronously). This is "
                        "transient — save again in a few seconds to attach the "
                        "remaining resources."
                    )
                else:
                    outcome.error_message = _extract_error(member_resp)

        # 3) Determine which current members to detach:
        #    - full sync (prune_to_members): every current member not desired,
        #      using the discovered membership as the source of truth;
        #    - otherwise: only the explicitly dropped resources.
        if prune_to_members:
            desired_norm = {norm_id(member.resource_id) for member in members}
            detach_targets: dict[str, List[dict]] = {
                source_id: rels
                for source_id, rels in existing_by_source.items()
                if source_id not in desired_norm
            }
        else:
            detach_targets = {
                resource_id: (existing_by_source.get(norm_id(resource_id)) or [])
                for resource_id in detach_resource_ids or []
            }

        for resource_id, rels in detach_targets.items():
            if not rels:
                # Not a member (per discovery) -> nothing to remove.
                outcome.detached.append(resource_id)
                continue

            detach_failed = False
            for rel in rels:
                try:
                    detach_resp = _delete(
                        client, token, rel["relationship_id"], SERVICE_GROUP_MEMBER_API_VERSION
                    )
                except httpx.HTTPError as exc:
                    outcome.failed.append(resource_id)
                    outcome.error_message = f"Member detach failed: {exc}"
                    detach_failed = True
                    break

                # 404 means the relationship is already gone -> treat as detached.
                if detach_resp.is_success or detach_resp.status_code == 404:
                    continue
                if detach_resp.status_code == 403:
                    outcome.permission_denied = True
                    outcome.error_message = _extract_error(detach_resp)
                else:
                    outcome.error_message = _extract_error(detach_resp)
                outcome.failed.append(resource_id)
                detach_failed = True
                break

            if not detach_failed:
                outcome.detached.append(resource_id)

    return outcome


def delete_service_group(service_group_name: str) -> ApplyOutcome:
    """Delete a Service Group and all of its member relationships from Azure.

    Member relationships are removed first (a Service Group cannot be cleanly
    removed while memberships remain), then the Service Group resource itself.
    A 404 at any step is treated as already-gone; a 403 is reported as
    ``permission_denied`` so the caller can surface a helpful message.
    ``detached`` carries the source ids of the members that were removed.
    """
    try:
        token = _acquire_token()
    except Exception as exc:  # credential/token acquisition failure
        return ApplyOutcome(error_message=f"Failed to acquire Azure token: {exc}")

    sg_path = _service_group_id(service_group_name)

    with httpx.Client() as client:
        try:
            existing = _get(client, token, sg_path, SERVICE_GROUP_API_VERSION)
        except httpx.HTTPError as exc:
            return ApplyOutcome(error_message=f"Service Group lookup failed: {exc}")

        if existing.status_code == 403:
            return ApplyOutcome(permission_denied=True, error_message=_extract_error(existing))
        if existing.status_code == 404:
            # Already gone — nothing to delete.
            return ApplyOutcome()

        outcome = ApplyOutcome()

        # 1) Remove every current member relationship (discovered from Azure
        #    Resource Graph) so the Service Group can be deleted without leaving
        #    dangling memberships behind.
        try:
            relationships = list_service_group_member_relationships(service_group_name)
        except Exception as exc:
            return ApplyOutcome(
                error_message=(
                    "Unable to read the Service Group's current members from Azure "
                    f"Resource Graph, which is required to delete it safely ({exc})."
                ),
            )

        for rel in relationships:
            try:
                del_resp = _delete(
                    client, token, rel["relationship_id"], SERVICE_GROUP_MEMBER_API_VERSION
                )
            except httpx.HTTPError as exc:
                outcome.error_message = f"Member detach failed: {exc}"
                outcome.failed.append(rel["source_id"])
                return outcome

            # 404 means the relationship is already gone -> treat as removed.
            if del_resp.is_success or del_resp.status_code == 404:
                outcome.detached.append(rel["source_id"])
                continue
            if del_resp.status_code == 403:
                outcome.permission_denied = True
            outcome.error_message = _extract_error(del_resp)
            outcome.failed.append(rel["source_id"])
            return outcome

        # 2) Delete the Service Group resource itself.
        try:
            sg_resp = _delete(client, token, sg_path, SERVICE_GROUP_API_VERSION)
        except httpx.HTTPError as exc:
            outcome.error_message = f"Service Group delete failed: {exc}"
            return outcome

        if sg_resp.is_success or sg_resp.status_code == 404:
            return outcome
        if sg_resp.status_code == 403:
            outcome.permission_denied = True
        outcome.error_message = _extract_error(sg_resp)
        return outcome
