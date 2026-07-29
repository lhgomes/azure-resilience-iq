#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as dt
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from azure.identity import DefaultAzureCredential


def _value(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _print_result(name: str, ok: bool, details: str = "") -> None:
    icon = "✓" if ok else "✗"
    print(f"{icon} {name}")
    if details:
        print(f"  {details}")


def _error_details(exc: Exception) -> str:
    message = str(exc).strip().replace("\n", " ")
    return message if message else repr(exc)


def test_connections_read(project_client: AIProjectClient) -> bool:
    try:
        conns = list(project_client.connections.list())
        _print_result("connections/read", True, f"listed {len(conns)} connection(s)")
        return True
    except (ClientAuthenticationError, HttpResponseError) as exc:
        _print_result("connections/read", False, _error_details(exc))
        return False


def test_agents_read(project_client: AIProjectClient) -> bool:
    try:
        agents = list(project_client.agents.list(limit=1))
        _print_result("agents/read", True, f"list call succeeded (returned {len(agents)} item(s))")
        return True
    except (ClientAuthenticationError, HttpResponseError) as exc:
        _print_result("agents/read", False, _error_details(exc))
        return False


def test_agents_write(project_client: AIProjectClient, model: str, cleanup: bool) -> bool:
    timestamp = dt.datetime.utcnow().strftime("%Y%m%d%H%M%S")
    agent_name = f"permcheck-agent-{timestamp}"
    created = None

    try:
        created = project_client.agents.create_version(
            agent_name=agent_name,
            definition=PromptAgentDefinition(
                model=model,
                instructions="Permission check agent. Safe to delete.",
            ),
            description="Permission check created by test_foundry_permissions.py",
        )
        created_id = str(_value(created, "id") or "")
        created_version = str(_value(created, "version") or "")
        _print_result(
            "agents/write",
            True,
            f"created agent '{agent_name}'"
            + (f", id={created_id}" if created_id else "")
            + (f", version={created_version}" if created_version else ""),
        )

        if cleanup:
            try:
                project_client.agents.delete(agent_name)
                _print_result("agents/delete (cleanup)", True, f"deleted '{agent_name}'")
            except (ClientAuthenticationError, HttpResponseError) as exc:
                _print_result("agents/delete (cleanup)", False, _error_details(exc))

        return True

    except (ClientAuthenticationError, HttpResponseError) as exc:
        _print_result("agents/write", False, _error_details(exc))
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate Foundry project managed-identity permissions for connections and agents"
    )
    parser.add_argument("--foundry-project-endpoint", required=True)
    parser.add_argument("--reasoning-model", default="gpt-5.4-mini")
    parser.add_argument(
        "--check-write",
        action="store_true",
        help="attempt to create an agent version to validate agents/write",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="delete the temporary permission-check agent after write test",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    endpoint = (args.foundry_project_endpoint or "").strip()
    if not endpoint:
        print("✗ Invalid --foundry-project-endpoint: value is empty")
        print("  Provide an https endpoint, for example:")
        print("  https://<resource>.services.ai.azure.com/api/projects/<project-name>")
        return 2
    if not endpoint.startswith("https://"):
        print(f"✗ Invalid --foundry-project-endpoint: '{endpoint}'")
        print("  Bearer token auth requires an https endpoint.")
        return 2

    credential = DefaultAzureCredential()
    project_client = AIProjectClient(endpoint=endpoint, credential=credential)

    print("Foundry Permission Check")
    print(f"Endpoint: {endpoint}")

    ok_connections = test_connections_read(project_client)
    ok_agents_read = test_agents_read(project_client)

    ok_agents_write = True
    if args.check_write:
        ok_agents_write = test_agents_write(
            project_client=project_client,
            model=args.reasoning_model,
            cleanup=args.cleanup,
        )
    else:
        print("- Skipping agents/write test (use --check-write to enable)")

    all_ok = ok_connections and ok_agents_read and ok_agents_write
    print("\nSummary:")
    _print_result("overall", all_ok)

    if not all_ok:
        print("\nSuggested minimum fix for this identity:")
        print("- Azure AI User on Foundry Hub scope")
        print("- Azure AI User on Foundry Project scope (if project-level checks fail)")
        print("- Wait for RBAC propagation and retry")

    return 0 if all_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
