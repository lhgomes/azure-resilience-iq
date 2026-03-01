#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Any

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AISearchIndexResource,
    AzureAISearchQueryType,
    AzureAISearchTool,
    AzureAISearchToolResource,
    ConnectionType,
    MCPTool,
    PromptAgentDefinition,
)
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ResourceNotFoundError
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchableField,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)

MICROSOFT_LEARN_MCP_SERVER_LABEL = "microsoft-learn"
MICROSOFT_LEARN_MCP_SERVER_URL = "https://learn.microsoft.com/api/mcp"


def _value(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _is_agents_read_permission_error(exc: HttpResponseError) -> bool:
    message = str(exc).lower()
    return (
        "workspaces/agents/read" in message
        or "does not have permissions" in message and "agents/read" in message
        or "authorizationfailed" in message and "agents" in message
    )


def _is_agents_write_permission_error(exc: HttpResponseError) -> bool:
    message = str(exc).lower()
    return (
        "workspaces/agents/write" in message
        or "workspaces/agents/create" in message
        or "does not have permissions" in message and "agents" in message
        or "authorizationfailed" in message and "agents" in message
    )


def _is_connections_read_permission_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "aiservices/connections/read" in message
        or "permissiondenied" in message and "connections" in message
        or "does not have permissions" in message and "connections" in message
    )


def _resolve_default_search_connection_id(project_client: AIProjectClient) -> str | None:
    try:
        connection = project_client.connections.get_default(ConnectionType.AZURE_AI_SEARCH)
        return str(_value(connection, "id") or "").strip() or None
    except ValueError:
        pass
    except (ClientAuthenticationError, HttpResponseError) as exc:
        if _is_connections_read_permission_error(exc):
            print(
                "⚠ Missing permission for Microsoft.CognitiveServices/accounts/AIServices/connections/read. "
                "Grant 'Azure AI User' on project scope and 'Reader' on Foundry hub/account scope, "
                "then retry after RBAC propagation. Continuing without Azure AI Search tool attachment for now."
            )
            return None
        raise

    try:
        for connection in project_client.connections.list():
            connection_id = str(_value(connection, "id") or "").strip()
            connection_type = str(_value(connection, "connection_type") or _value(connection, "type") or "").lower()
            target = str(_value(connection, "target") or "").lower()
            endpoint = str(_value(connection, "endpoint") or "").lower()

            if not connection_id:
                continue

            if (
                "cognitivesearch" in connection_type
                or "azure_ai_search" in connection_type
                or "search.windows.net" in target
                or "search.windows.net" in endpoint
            ):
                print(f"⚠ No default Azure AI Search connection set; using available connection: {connection_id}")
                return connection_id
    except (ClientAuthenticationError, HttpResponseError) as exc:
        if _is_connections_read_permission_error(exc):
            print(
                "⚠ Missing permission for Microsoft.CognitiveServices/accounts/AIServices/connections/read. "
                "Grant 'Azure AI User' on project scope and 'Reader' on Foundry hub/account scope, "
                "then retry after RBAC propagation. Continuing without Azure AI Search tool attachment for now."
            )
            return None
        raise

    print("⚠ No Azure AI Search project connection found; continuing without Azure AI Search tool attachment.")
    return None


def _build_index_definition(index_name: str, embedding_dimensions: int) -> SearchIndex:
    vector_profile_name = "vector-profile"
    vector_algo_name = "hnsw-default"

    return SearchIndex(
        name=index_name,
        fields=[
            SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),
            SearchableField(name="title", type=SearchFieldDataType.String),
            SearchableField(name="content", type=SearchFieldDataType.String),
            SimpleField(name="corpus", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="source", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="source_type", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="chunk_no", type=SearchFieldDataType.Int32, filterable=True, sortable=True),
            SearchField(
                name="content_vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=embedding_dimensions,
                vector_search_profile_name=vector_profile_name,
            ),
        ],
        vector_search=VectorSearch(
            algorithms=[
                HnswAlgorithmConfiguration(name=vector_algo_name),
            ],
            profiles=[
                VectorSearchProfile(
                    name=vector_profile_name,
                    algorithm_configuration_name=vector_algo_name,
                )
            ],
        ),
    )


def ensure_search_indexes(
    *,
    search_endpoint: str,
    aprl_index_name: str,
    terraform_index_name: str,
    embedding_dimensions: int,
) -> None:
    client = SearchIndexClient(search_endpoint, DefaultAzureCredential())

    for index_name in [aprl_index_name, terraform_index_name]:
        definition = _build_index_definition(index_name, embedding_dimensions)
        try:
            client.create_or_update_index(definition)
            print(f"✓ Search index ensured: {index_name}")
        except HttpResponseError as exc:
            err = str(exc)
            if "CannotChangeExistingField" in err or "Existing field" in err:
                print(f"⚠ Search index exists with immutable schema differences; keeping current index: {index_name}")
                continue
            raise


def _load_agent_instruction_map(agent_dir: Path) -> Dict[str, str]:
    return {
        "chat-agent": (agent_dir / "chat-agent.txt").read_text(encoding="utf-8"),
        "resilience-agent": (agent_dir / "resilience-agent.txt").read_text(encoding="utf-8"),
        "annotations-agent": (agent_dir / "annotations-agent.txt").read_text(encoding="utf-8"),
        "terraform-compiler-agent": (agent_dir / "terraform-compiler-agent.txt").read_text(encoding="utf-8"),
    }


def _agent_tool_profile(agent_name: str) -> Dict[str, bool]:
    """Requested tool mapping per agent.

    - terraform-compiler-agent: Azure AI Search (terraform index)
    - chat-agent: Azure AI Search (aprl index) + MCP
    - resilience-agent: Azure AI Search (aprl index) + MCP
    - annotations-agent: no tools
    """
    mapping = {
        "terraform-compiler-agent": {"search": True, "mcp": False},
        "chat-agent": {"search": True, "mcp": True},
        "resilience-agent": {"search": True, "mcp": True},
        "annotations-agent": {"search": False, "mcp": False},
    }
    return mapping.get(agent_name, {"search": False, "mcp": False})


def _resolve_microsoft_learn_connection_id(project_client: AIProjectClient) -> str | None:
    """Best-effort resolution of Microsoft Learn project connection id.

    Uses existing project connections when available; falls back to direct MCP URL without
    a project connection id when no suitable connection is present.
    """
    try:
        for connection in project_client.connections.list():
            connection_id = str(_value(connection, "id") or "").strip()
            connection_name = str(_value(connection, "name") or "").strip().lower()
            target = str(_value(connection, "target") or "").strip().lower()
            endpoint = str(_value(connection, "endpoint") or "").strip().lower()

            if "microsoftlearn" in connection_name:
                return connection_id or None

            if "learn.microsoft.com/api/mcp" in target or "learn.microsoft.com/api/mcp" in endpoint:
                return connection_id or None
    except Exception:
        pass

    return None


def _build_agent_tools(
    *,
    project_client: AIProjectClient,
    agent_name: str,
    aprl_index_name: str,
    terraform_index_name: str,
    microsoft_learn_connection_id: str | None,
    search_connection_id: str | None,
) -> list[Any]:
    profile = _agent_tool_profile(agent_name)
    if not profile["search"] and not profile["mcp"]:
        return []

    tools: list[Any] = []

    if profile["search"]:
        if not search_connection_id:
            print(f"⚠ Skipping Azure AI Search tool for {agent_name}: no readable project search connection.")
        else:
            if agent_name == "terraform-compiler-agent":
                index_name = terraform_index_name
            else:
                index_name = aprl_index_name

            tools.append(
                AzureAISearchTool(
                    azure_ai_search=AzureAISearchToolResource(
                        indexes=[
                            AISearchIndexResource(
                                project_connection_id=search_connection_id,
                                index_name=index_name,
                                query_type=AzureAISearchQueryType.SIMPLE,
                                top_k=5,
                            )
                        ]
                    )
                )
            )

    if profile["mcp"]:
        try:
            tools.append(
                MCPTool(
                    server_label=MICROSOFT_LEARN_MCP_SERVER_LABEL,
                    server_url=MICROSOFT_LEARN_MCP_SERVER_URL,
                    allowed_tools=[],
                    project_connection_id=microsoft_learn_connection_id,
                )
            )
        except Exception as exc:
            print(f"⚠ MCP tool attachment skipped: {exc}")

    return tools


def ensure_foundry_agents(
    *,
    foundry_project_endpoint: str,
    reasoning_model: str,
    agent_dir: Path,
    recreate_existing: bool,
    aprl_index_name: str,
    terraform_index_name: str,
) -> Dict[str, str]:
    credential = DefaultAzureCredential()
    project_client = AIProjectClient(endpoint=foundry_project_endpoint, credential=credential)
    agents_client = project_client.agents

    instructions = _load_agent_instruction_map(agent_dir)
    try:
        existing = {str(_value(agent, "name")): agent for agent in agents_client.list(limit=200)}
    except ResourceNotFoundError:
        print(
            "⚠ Agents management API returned Not Found for this project endpoint. "
            "Proceeding with stable agent name references without create/list operations."
        )
        return {
            "chat-agent": "chat-agent",
            "resilience-agent": "resilience-agent",
            "annotations-agent": "annotations-agent",
            "terraform-compiler-agent": "terraform-compiler-agent",
        }
    except HttpResponseError as exc:
        if _is_agents_read_permission_error(exc):
            print(
                "⚠ Missing permission for Microsoft.MachineLearningServices/workspaces/agents/read. "
                "Proceeding without list/reconcile and attempting direct agent version create operations."
            )
            if recreate_existing:
                print(
                    "⚠ --recreate-existing requested, but existing agents cannot be listed with current permissions; "
                    "skipping delete/recreate and using create-version behavior."
                )
            existing = {}
        else:
            raise
    microsoft_learn_connection_id = _resolve_microsoft_learn_connection_id(project_client)
    if microsoft_learn_connection_id:
        print(f"✓ Resolved Microsoft Learn project connection: {microsoft_learn_connection_id}")
    else:
        print("⚠ Microsoft Learn project connection not found; using official MCP endpoint directly.")
    search_connection_id = _resolve_default_search_connection_id(project_client)
    if search_connection_id:
        print(f"✓ Resolved default Azure AI Search project connection: {search_connection_id}")

    result: Dict[str, str] = {}
    for agent_name, agent_instructions in instructions.items():
        current = existing.get(agent_name)
        if current and recreate_existing:
            agents_client.delete(agent_name)
            current = None

        if current:
            agent_id = str(_value(current, "id"))
            print(f"✓ Found existing agent: {agent_name} ({agent_id})")
            result[agent_name] = agent_id
            continue

        tools = _build_agent_tools(
            project_client=project_client,
            agent_name=agent_name,
            aprl_index_name=aprl_index_name,
            terraform_index_name=terraform_index_name,
            microsoft_learn_connection_id=microsoft_learn_connection_id,
            search_connection_id=search_connection_id,
        )

        definition = PromptAgentDefinition(
            model=reasoning_model,
            instructions=agent_instructions,
            tools=tools or None,
        )

        try:
            created = agents_client.create_version(
                agent_name=agent_name,
                definition=definition,
                description=f"{agent_name} provisioned by automation",
            )
        except HttpResponseError as exc:
            if _is_agents_write_permission_error(exc):
                print(
                    "✗ Missing permission for Microsoft.MachineLearningServices/workspaces/agents/write. "
                    "Grant 'Azure AI User' on project scope (and 'Reader' on hub/account scope) to the deployment identity."
                )
            raise
        created_id = str(_value(created, "id") or "")
        created_version = str(_value(created, "version") or "")
        if created_version:
            print(f"✓ Created/updated agent version: {agent_name} (version: {created_version})")
        elif created_id:
            print(f"✓ Created/updated agent: {agent_name} ({created_id})")
        else:
            print(f"✓ Created/updated agent: {agent_name}")
        print(
            "  tools="
            f"search:{_agent_tool_profile(agent_name)['search']} "
            f"mcp:{_agent_tool_profile(agent_name)['mcp']}"
        )
        result[agent_name] = agent_name

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Provision Foundry agents and Azure Search indexes")
    parser.add_argument("--foundry-project-endpoint", required=True)
    parser.add_argument("--reasoning-model", default="gpt-4.1")
    parser.add_argument("--search-endpoint", required=True)
    parser.add_argument("--aprl-index-name", default="learn-aprl-index")
    parser.add_argument("--terraform-index-name", default="learn-terraform-index")
    parser.add_argument("--embedding-dimensions", type=int, default=1536)
    parser.add_argument(
        "--agent-dir",
        default=str(Path(__file__).resolve().parents[2] / "agent"),
    )
    parser.add_argument("--recreate-existing", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print("\nProvisioning Foundry agents and Azure Search indexes with the following configuration:")
    print(f"Foundry project endpoint: {args.foundry_project_endpoint}")
    print(f"Reasoning model: {args.reasoning_model}")
    print(f"Search endpoint: {args.search_endpoint}")
    print(f"APRL index name: {args.aprl_index_name}")
    print(f"Terraform index name: {args.terraform_index_name}")
    print(f"Embedding dimensions: {args.embedding_dimensions}")
    print(f"Agent directory: {args.agent_dir}")
    print(f"Recreate existing agents: {args.recreate_existing}")

    ensure_search_indexes(
        search_endpoint=args.search_endpoint,
        aprl_index_name=args.aprl_index_name,
        terraform_index_name=args.terraform_index_name,
        embedding_dimensions=args.embedding_dimensions,
    )

    created_agents = ensure_foundry_agents(
        foundry_project_endpoint=args.foundry_project_endpoint,
        reasoning_model=args.reasoning_model,
        agent_dir=Path(args.agent_dir),
        recreate_existing=args.recreate_existing,
        aprl_index_name=args.aprl_index_name,
        terraform_index_name=args.terraform_index_name,
    )

    print("\nAgent references:")
    print(f"AI_FOUNDRY_CHAT_AGENT_REFERENCE={created_agents.get('chat-agent', '')}")
    print(f"AI_FOUNDRY_RESILIENCE_AGENT_REFERENCE={created_agents.get('resilience-agent', '')}")
    print(f"AI_FOUNDRY_ANNOTATIONS_AGENT_REFERENCE={created_agents.get('annotations-agent', '')}")
    print(f"AI_FOUNDRY_TERRAFORM_AGENT_REFERENCE={created_agents.get('terraform-compiler-agent', '')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
