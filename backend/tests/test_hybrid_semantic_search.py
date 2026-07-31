from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "deploy" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import provision_foundry_assets as foundry_assets


def test_index_definition_enables_query_vectorization_and_semantic_ranking() -> None:
    index = foundry_assets._build_index_definition(
        index_name="test-index",
        embedding_dimensions=1536,
        embedding_endpoint="https://example.openai.azure.com",
        embedding_deployment="text-embedding-3-small",
        embedding_model="text-embedding-3-small",
    )

    profile = index.vector_search.profiles[0]
    vectorizer = index.vector_search.vectorizers[0]
    semantic = index.semantic_search

    assert profile.vectorizer_name == "foundry-embedding-vectorizer"
    assert vectorizer.parameters.resource_url == "https://example.openai.azure.com"
    assert vectorizer.parameters.deployment_name == "text-embedding-3-small"
    assert semantic.default_configuration_name == "rag-semantic-config"
    assert semantic.configurations[0].prioritized_fields.title_field.field_name == "title"
    assert semantic.configurations[0].prioritized_fields.content_fields[0].field_name == "content"


def test_search_tools_use_vector_semantic_hybrid_queries() -> None:
    tools = foundry_assets._build_agent_tools(
        project_client=object(),
        agent_name="terraform-compiler-agent",
        aprl_index_name="aprl-index",
        terraform_index_name="terraform-index",
        microsoft_learn_connection_id=None,
        search_connection_id="search-connection",
    )

    index_resource = tools[0].azure_ai_search.indexes[0]
    assert index_resource.query_type == foundry_assets.AzureAISearchQueryType.VECTOR_SEMANTIC_HYBRID
    assert index_resource.index_name == "terraform-index"