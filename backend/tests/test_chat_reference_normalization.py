from __future__ import annotations

import asyncio

import pytest

from app.chat.service import ChatService
from app.chat.models import ChatResponse


def _service_without_dependencies() -> ChatService:
    return ChatService.__new__(ChatService)


def test_compact_graph_references_are_canonicalized_without_changing_answer() -> None:
    service = _service_without_dependencies()
    resource_id = "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Search/searchServices/search-1"
    short_id = "745d654a-38da-5b47-a7a2-2dbf9f4bddef"
    llm_output = {
        "message": "Improve resilience by increasing Search replicas.",
        "resources_to_highlight": ["search-1"],
        "criticality_insights": [{"node_id": short_id, "suggested_score": 8}],
        "suggested_edges": [{"source": short_id, "target": resource_id, "relationship": "depends_on"}],
        "recommendations": [{"recommendation_id": "rec-1", "title": "Increase replicas"}],
    }
    graph = {
        "nodes": [
            {
                "id": resource_id,
                "short_id": short_id,
                "name": "search-1",
                "metadata": {"display_name": "Search One"},
            }
        ]
    }

    normalized, unresolved = service._canonicalize_llm_references(llm_output, graph)

    assert unresolved == []
    assert normalized["message"] == llm_output["message"]
    assert normalized["recommendations"] == llm_output["recommendations"]
    assert normalized["resources_to_highlight"] == [resource_id]
    assert normalized["criticality_insights"][0]["node_id"] == resource_id
    assert normalized["suggested_edges"][0]["source"] == resource_id
    assert normalized["suggested_edges"][0]["target"] == resource_id


def test_ambiguous_or_unknown_references_are_filtered() -> None:
    service = _service_without_dependencies()
    graph = {
        "nodes": [
            {"id": "/subscriptions/sub-1/resources/one", "name": "duplicate"},
            {"id": "/subscriptions/sub-1/resources/two", "name": "duplicate"},
        ]
    }

    normalized, unresolved = service._canonicalize_llm_references(
        {
            "message": "Keep this answer.",
            "resources_to_highlight": ["duplicate", "unknown"],
            "criticality_insights": [],
            "suggested_edges": [],
        },
        graph,
    )

    assert normalized["message"] == "Keep this answer."
    assert normalized["resources_to_highlight"] == []
    assert len(unresolved) == 2


def test_process_query_normalizes_references_with_one_agent_call(monkeypatch) -> None:
    resource_id = "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Search/searchServices/search-1"
    short_id = "745d654a-38da-5b47-a7a2-2dbf9f4bddef"

    class Gateway:
        calls = 0

        @staticmethod
        def is_available() -> bool:
            return True

        def generate_json(self, **_kwargs):
            self.calls += 1
            return {
                "message": "Increase Search replicas.",
                "resources_to_highlight": [short_id],
                "criticality_insights": [],
                "suggested_edges": [],
                "recommendations": [],
            }

        @staticmethod
        def get_last_metrics():
            return {}

    service = _service_without_dependencies()
    service.llm_gateway = Gateway()
    service.llm_generation_config = {"max_tokens": 2000}
    monkeypatch.setattr(service, "_resolve_agent_for_query_type", lambda _query_type: ("chat", "chat-agent"))
    monkeypatch.setattr(
        service,
        "_resolve_conversation_scope",
        lambda *_args: {
            "scope_type": "subscription",
            "scope_id": "sub-1",
            "anchor_subscription_id": "sub-1",
        },
    )
    monkeypatch.setattr(service, "_load_conversation_id", lambda *_args: None)
    monkeypatch.setattr(service, "_is_conversation_context_seeded", lambda *_args: False)
    monkeypatch.setattr(service, "_classify_query_scope", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(service, "_collect_referenced_resource_ids", lambda **_kwargs: [])
    monkeypatch.setattr(service, "_build_prompt", lambda *_args, **_kwargs: "prompt")
    monkeypatch.setattr(service, "_system_prompt", lambda *_args, **_kwargs: "system")
    monkeypatch.setattr(service, "_mark_conversation_context_seeded", lambda **_kwargs: None)
    monkeypatch.setattr(
        service,
        "_process_llm_response",
        lambda output, *_args, **_kwargs: ChatResponse(
            message=output["message"],
            resources_to_highlight=output["resources_to_highlight"],
        ),
    )
    monkeypatch.setattr(service, "_append_cross_flow_handoff", lambda **_kwargs: None)

    response = asyncio.run(
        service.process_query(
            query="How can I improve resilience?",
            graph={"nodes": [{"id": resource_id, "short_id": short_id}], "edges": []},
            subscription_id="sub-1",
        )
    )

    assert service.llm_gateway.calls == 1
    assert response.message == "Increase Search replicas."
    assert response.resources_to_highlight == [resource_id]


def test_cross_flow_handoff_is_added_to_prompt() -> None:
    service = _service_without_dependencies()

    prompt = service._build_prompt(
        query="Generate Terraform for the selected recommendation",
        graph={"nodes": [], "edges": []},
        subscription_id="sub-1",
        context=None,
        query_type="terraform",
        referenced_resource_ids=None,
        include_full_context=False,
        cross_flow_handoffs=[
            {
                "flow": "chat",
                "user_intent": "Improve Search resilience",
                "answer_summary": "Increase replicas.",
                "resource_ids": ["745d654a-38da-5b47-a7a2-2dbf9f4bddef"],
                "recommendation_ids": ["rec-1"],
                "clarifying_questions": [],
            }
        ],
    )

    assert "CROSS-FLOW HANDOFF MEMORY" in prompt
    assert "flow=chat" in prompt
    assert "resources=745d654a-38da-5b47-a7a2-2dbf9f4bddef" in prompt
    assert "recommendations=rec-1" in prompt


def test_cross_flow_handoff_is_not_replayed_for_clarification_answer() -> None:
    service = _service_without_dependencies()

    prompt = service._build_prompt(
        query="Clarification answers:\n1. State ownership: Existing Terraform state",
        graph={"nodes": [], "edges": []},
        subscription_id="sub-1",
        context=None,
        query_type="terraform",
        referenced_resource_ids=None,
        include_full_context=False,
        cross_flow_handoffs=[{"flow": "chat", "answer_summary": "Increase replicas."}],
    )

    assert "CROSS-FLOW HANDOFF MEMORY" not in prompt


def test_referenced_resource_context_uses_bounded_operational_facts(monkeypatch) -> None:
    service = _service_without_dependencies()
    resource_id = "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Search/searchServices/search-1"
    monkeypatch.setattr(
        service,
        "_load_resource_index",
        lambda _subscription_id: {
            resource_id.casefold(): {
                "id": resource_id,
                "name": "search-1",
                "type": "microsoft.search/searchservices",
                "location": "eastus",
                "sku": {"name": "standard"},
                "properties": {"large_unneeded_payload": "x" * 50000},
            }
        },
    )
    monkeypatch.setattr(service, "_load_node_override_index", lambda _subscription_id: {})

    context = service._build_referenced_resource_context([resource_id], "sub-1")

    assert '"name": "search-1"' in context
    assert '"sku_name": "standard"' in context
    assert "large_unneeded_payload" not in context
    assert len(context) < 2000


def test_terraform_preflight_forces_available_resource_facts(monkeypatch) -> None:
    service = _service_without_dependencies()
    monkeypatch.setattr(service, "_summarize_failed_findings", lambda *_args, **_kwargs: "failed checks")
    monkeypatch.setattr(service, "_build_referenced_resource_context", lambda **_kwargs: "None.")
    captured = {}

    def build_details(**kwargs):
        captured.update(kwargs)
        return '- search-1: {"location":"eastus","sku_name":"standard"}'

    monkeypatch.setattr(service, "_build_detailed_resource_context", build_details)

    prompt = service._build_terraform_preflight_prompt(
        query="Give me Terraform to improve resilience",
        graph={},
        subscription_id="sub-1",
        context=None,
        referenced_resource_ids=[],
    )

    assert captured["force"] is True
    assert '"location":"eastus"' in prompt
    assert "side-by-side net-new architecture" in prompt
    assert "Do not ask for destructive-change permission" in prompt


def test_terraform_catalog_prompt_uses_native_net_new_fallback_without_evidence() -> None:
    service = _service_without_dependencies()

    prompt = service._append_terraform_catalog_evidence("Compile Terraform", [])

    assert "Use native azurerm resources" in prompt
    assert "source_refs: []" in prompt
    assert "side-by-side net-new resilient architecture" in prompt
    assert "return files: []" not in prompt


def test_terraform_catalog_prompt_keeps_native_fallback_with_module_evidence() -> None:
    service = _service_without_dependencies()

    prompt = service._append_terraform_catalog_evidence(
        "Compile Terraform",
        [{"ref": "search:index:doc-1", "title": "Storage AVM", "source": "AVM", "content": "module"}],
    )

    assert "AVM/CAF module claims" in prompt
    assert "native azurerm resources with source_refs: []" in prompt


@pytest.mark.parametrize(
    "question",
    [
        "For the storage account, what exact zone-redundant SKU should be used if supported in the current region?",
        "For the storage account update target, should the new desired SKU be Standard_ZRS even if this requires a region/SKU capability check outside Terraform?",
        "Should I proceed with Standard_ZRS as the desired storage account SKU even though region/SKU capability is not verified in the retrieved evidence?",
        "Do you want me to proceed with Standard_ZRS for the storage account even though zone-redundant support is not validated in the retrieved evidence for your region and account type?",
    ],
)
def test_terraform_platform_decisions_are_not_user_questions(question: str) -> None:
    service = _service_without_dependencies()

    assert service._normalize_terraform_clarifying_questions([
        {"question": question, "possible_answers": ["Yes", "No"]}
    ]) == []


@pytest.mark.parametrize(
    "question",
    [
        "Please provide the current HCL for the existing storage account.",
        "Is the existing storage account already managed in Terraform state?",
        "What is the current SKU deployed on the storage account?",
        "Which Azure regions are permitted by your data residency policy?",
    ],
)
def test_terraform_user_owned_inputs_remain_valid_questions(question: str) -> None:
    service = _service_without_dependencies()

    questions = service._normalize_terraform_clarifying_questions([
        {"question": question, "possible_answers": []}
    ])

    assert [item["question"] for item in questions] == [question]


def test_resource_facts_include_storage_account_kind() -> None:
    service = _service_without_dependencies()

    facts = service._extract_resource_facts({
        "name": "storage-1",
        "type": "microsoft.storage/storageaccounts",
        "kind": "StorageV2",
        "location": "eastus",
        "sku": {"name": "Standard_LRS"},
        "properties": {},
    })

    assert facts["kind"] == "StorageV2"
    assert facts["sku_name"] == "Standard_LRS"


def test_clarification_answer_query_is_detected() -> None:
    service = _service_without_dependencies()

    assert service._is_clarification_answer_query("Clarification answers:\n1. Use ZRS")
    assert not service._is_clarification_answer_query("Can you generate Terraform?")
    assert service._is_terraform_finalization_query("Proceed anyway and use defaults.")


def test_clarification_answer_prompt_is_finalization_turn() -> None:
    service = _service_without_dependencies()

    prompt = service._build_prompt(
        query="Clarification answers:\n1. Use ZRS",
        graph={"nodes": [], "edges": []},
        subscription_id="sub-1",
        context=None,
        query_type="terraform",
        referenced_resource_ids=None,
        include_full_context=False,
        cross_flow_handoffs=[],
    )

    assert "TERRAFORM FINALIZATION MODE" in prompt
    assert "Do not return more clarifying questions" in prompt
    assert "Never guess existing resource arguments" in prompt


@pytest.mark.parametrize(
    "query",
    [
        "Clarification answers:\n1. Use ZRS",
        "Proceed anyway and use defaults.",
    ],
)
def test_clarification_answer_cannot_trigger_another_question_round(query: str) -> None:
    service = _service_without_dependencies()
    llm_output = {
        "message": "I need a few more details.",
        "clarifying_questions": [
            {
                "question": "What is the current SKU?",
                "possible_answers": ["Standard", "Premium"],
            }
        ],
        "files": [{"filename": "main.tf", "content": "resource \"azurerm_search_service\" \"main\" {}"}],
    }

    response = service._process_llm_response(
        llm_output,
        {"nodes": [], "edges": []},
        query=query,
        query_type="terraform",
        flow="terraform",
        include_rag_trace=False,
        target_agent_id="terraform-agent",
        scope_type="subscription",
        scope_id="sub-1",
        conversation_id="conv-1",
        trace_id="trace-1",
    )

    assert response.clarifying_questions == []
    assert response.terraform_code is None
    assert response.raw_llm_output["files"] == []
    assert response.message == "I could not safely generate Terraform from the supplied details."
    assert "stopped after one clarification round" in response.terraform_validation


def test_structured_terraform_blocker_is_never_rendered_as_no_response() -> None:
    service = _service_without_dependencies()
    llm_output = {
        "files": [],
        "module_decisions": [],
        "clarifying_questions": [],
        "rag_trace": {
            "retrieval_status": "partial",
            "notes": [
                "Retrieved evidence did not include verified Terraform interfaces for the required controls.",
                "Native azurerm resource schemas were unavailable, so deterministic compilation is blocked.",
            ],
        },
        "confidence": 0.28,
    }

    response = service._process_llm_response(
        llm_output,
        {"nodes": [], "edges": []},
        query="Clarification answers:\n1. Import existing resources",
        query_type="terraform",
        flow="terraform",
        include_rag_trace=False,
        target_agent_id="terraform-agent",
        scope_type="subscription",
        scope_id="sub-1",
        conversation_id="conv-1",
        trace_id="trace-1",
    )

    assert response.message == "Terraform was not generated because the compiler returned no configuration files."
    assert "compiler returned no configuration files" in response.terraform_validation
    assert "Retrieved evidence" not in response.terraform_validation
    assert response.clarifying_questions == []
    assert response.terraform_code is None


def test_workload_conversation_rotation_clears_subscription_fallback(monkeypatch) -> None:
    service = _service_without_dependencies()
    cleared = []
    seeded = []
    fingerprints = []
    monkeypatch.setattr(service, "_clear_conversation_id", lambda scope_type, scope_id, flow: cleared.append((scope_type, scope_id, flow)))
    monkeypatch.setattr(
        service,
        "_set_conversation_context_seeded",
        lambda scope_type, scope_id, flow, value: seeded.append((scope_type, scope_id, flow, value)),
    )
    monkeypatch.setattr(
        service,
        "_clear_conversation_context_fingerprint",
        lambda scope_type, scope_id, flow: fingerprints.append((scope_type, scope_id, flow)),
    )

    service._clear_conversation_state(
        scope_type="workload",
        scope_id="workload-1",
        anchor_subscription_id="sub-1",
        flow="terraform",
    )

    assert cleared == [
        ("workload", "workload-1", "terraform"),
        ("subscription", "sub-1", "terraform"),
    ]
    assert seeded == [
        ("workload", "workload-1", "terraform", False),
        ("subscription", "sub-1", "terraform", False),
    ]
    assert fingerprints == [
        ("workload", "workload-1", "terraform"),
        ("subscription", "sub-1", "terraform"),
    ]


def test_terraform_validation_requires_model_deployment_for_model_findings() -> None:
    service = _service_without_dependencies()
    graph = {
        "resilience_evaluations": {
            "evaluations": {
                "resource-1": {
                    "checks": [
                        {
                            "status": "fail",
                            "description": "Ensure PAYG AOAI models leverage Global Standard deployment",
                        }
                    ]
                }
            }
        }
    }

    validation = service._validate_terraform_against_findings(
        'resource "azurerm_cognitive_account" "existing" {}',
        graph,
    )

    assert "azurerm_cognitive_deployment" in validation


def test_terraform_validation_rejects_standard_sku_for_global_model_findings() -> None:
    service = _service_without_dependencies()
    graph = {
        "resilience_evaluations": {
            "evaluations": {
                "resource-1": {
                    "checks": [
                        {
                            "status": "fail",
                            "description": "Ensure PAYG AOAI models leverage Global Standard deployment",
                        },
                        {
                            "status": "fail",
                            "description": "Leverage Global provisioned deployment for predictable throughput",
                        },
                    ]
                }
            }
        }
    }

    validation = service._validate_terraform_against_findings(
        'resource "azurerm_cognitive_deployment" "existing" { scale { type = "Standard" } }',
        graph,
    )

    assert "GlobalStandard" in validation
    assert "GlobalProvisionedManaged" in validation


def test_terraform_validation_requires_all_cross_resource_controls() -> None:
    service = _service_without_dependencies()
    graph = {
        "resilience_evaluations": {
            "evaluations": {
                "search-1": {
                    "checks": [
                        {"status": "fail", "description": "Enable Multi Region deployments for AI Search"},
                    ]
                },
                "subscription-1": {
                    "checks": [
                        {"status": "fail", "description": "Configure Service Health Alerts"},
                        {
                            "status": "fail",
                            "description": "Ensure Resource Group and its Resources are located in the same Region",
                        },
                    ]
                },
            }
        }
    }

    validation = service._validate_terraform_against_findings(
        'resource "azurerm_search_service" "primary" { replica_count = 2 }',
        graph,
    )

    assert "multi-region" in validation.lower()
    assert "service health" in validation.lower()
    assert "region alignment" in validation.lower()


def test_terraform_validation_rejects_resource_group_location_mismatch() -> None:
    service = _service_without_dependencies()
    graph = {
        "resilience_evaluations": {
            "evaluations": {
                "subscription-1": {
                    "checks": [{
                        "status": "fail",
                        "description": "Ensure Resource Group and its Resources are located in the same Region",
                    }]
                }
            }
        }
    }
    code = '''
variable "primary_location" { default = "uaenorth" }
variable "secondary_location" { default = "uaecentral" }

resource "azurerm_resource_group" "ai" {
  name     = "ai-rg"
  location = var.primary_location
}

resource "azurerm_search_service" "secondary" {
  location            = var.secondary_location
  resource_group_name = azurerm_resource_group.ai.name
}
'''

    validation = service._validate_terraform_against_findings(code, graph)

    assert "azurerm_search_service.secondary" in validation


def test_terraform_validation_accepts_matching_resource_group_locations() -> None:
    service = _service_without_dependencies()
    graph = {
        "resilience_evaluations": {
            "evaluations": {
                "subscription-1": {
                    "checks": [{
                        "status": "fail",
                        "description": "Ensure Resource Group and its Resources are located in the same Region",
                    }]
                }
            }
        }
    }
    code = '''
variable "primary_location" { default = "uaenorth" }

resource "azurerm_resource_group" "ai" {
  name     = "ai-rg"
  location = var.primary_location
}

resource "azurerm_search_service" "primary" {
  location            = azurerm_resource_group.ai.location
  resource_group_name = azurerm_resource_group.ai.name
}
'''

    assert service._validate_terraform_against_findings(code, graph) is None


def test_terraform_validation_accepts_search_replica_variable_default() -> None:
    service = _service_without_dependencies()
    graph = {
        "resilience_evaluations": {
            "evaluations": {
                "search": {
                    "checks": [
                        {
                            "status": "fail",
                            "description": "Enable AZ support in AI Search by configuring multiple replicas",
                        }
                    ]
                }
            }
        }
    }
    code = '''
variable "search_replica_count" {
  type    = number
  default = 2
}

resource "azurerm_search_service" "primary" {
  replica_count = var.search_replica_count
}
'''

    assert service._validate_terraform_against_findings(code, graph) is None


def test_terraform_validation_accepts_native_fallback_without_catalog_evidence() -> None:
    service = _service_without_dependencies()

    validation = service._validate_terraform_against_findings(
        'resource "azurerm_storage_account" "existing" {}',
        {},
        llm_output={
            "rag_trace": {"retrieval_status": "none"},
            "module_decisions": [
                {
                    "decision": "native_azurerm",
                    "module_or_resource": "azurerm_storage_account",
                    "source_refs": [],
                }
            ],
        },
    )

    assert validation is None


def test_terraform_validation_rejects_unevidenced_avm_module() -> None:
    service = _service_without_dependencies()

    validation = service._validate_terraform_against_findings(
        'module "storage" { source = "Azure/avm-res-storage-storageaccount/azurerm" }',
        {},
        llm_output={
            "rag_trace": {"retrieval_status": "none"},
            "module_decisions": [
                {
                    "decision": "avm",
                    "module_or_resource": "Azure/avm-res-storage-storageaccount/azurerm",
                    "source_refs": [],
                }
            ],
        },
    )

    assert "AVM/CAF" in validation
    assert "catalog retrieval" in validation


def test_terraform_catalog_retrieval_uses_bounded_hybrid_semantic_query(monkeypatch) -> None:
    service = _service_without_dependencies()
    captured = {}

    class SearchClientStub:
        def search(self, **kwargs):
            captured.update(kwargs)
            return [
                {
                    "id": "doc-1",
                    "title": "Storage module",
                    "source": "AVM",
                    "source_type": "avm",
                    "content": "x" * 3000,
                }
            ]

    monkeypatch.setattr(service, "_get_terraform_search_client", lambda: SearchClientStub())
    monkeypatch.setenv("AZURE_SEARCH_INDEX_NAME_TERRAFORM", "learn-terraform-hybrid-index")

    evidence = service._retrieve_terraform_catalog_evidence(
        query="Improve resilience with Terraform",
        graph={
            "resilience_evaluations": {
                "evaluations": {
                    "resource-1": {
                        "checks": [
                            {"status": "fail", "description": "Enable storage soft delete"},
                        ]
                    }
                }
            }
        },
    )

    assert captured["query_type"] == "semantic"
    assert captured["semantic_configuration_name"] == "rag-semantic-config"
    assert captured["top"] == 5
    assert captured["vector_queries"][0].fields == "content_vector"
    assert "Enable storage soft delete" in captured["search_text"]
    assert evidence[0]["ref"] == "search:learn-terraform-hybrid-index:doc-1"
    assert len(evidence[0]["content"]) == 1600


def test_terraform_validation_rejects_malformed_hcl() -> None:
    service = _service_without_dependencies()
    output = {
        "files": [
            {
                "filename": "main.tf",
                "content": 'resource "azurerm_storage_account" "broken" {',
            }
        ],
        "_backend_retrieval_evidence": [{"ref": "search:index:doc-1"}],
        "rag_trace": {"retrieval_status": "grounded"},
        "module_decisions": [
            {
                "target": "storage",
                "decision": "native_azurerm",
                "module_or_resource": "azurerm_storage_account",
                "source_refs": ["search:index:doc-1"],
            }
        ],
    }

    validation = service._validate_terraform_against_findings(
        output["files"][0]["content"],
        {},
        llm_output=output,
    )

    assert "valid hcl" in validation.lower()


def test_persisted_handoff_uses_short_resource_ids(monkeypatch) -> None:
    service = _service_without_dependencies()
    captured = {}
    monkeypatch.setattr(
        "app.chat.service.append_cross_flow_handoff",
        lambda **kwargs: captured.update(kwargs),
    )
    resource_id = "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Search/searchServices/search-1"
    short_id = "745d654a-38da-5b47-a7a2-2dbf9f4bddef"

    service._append_cross_flow_handoff(
        scope_type="subscription",
        scope_id="sub-1",
        flow="chat",
        context_fingerprint="fingerprint-1",
        query="How can I improve resilience?",
        response=ChatResponse(
            message="Increase Search replicas.",
            resources_to_highlight=[resource_id],
            recommendations=[{"recommendation_id": "rec-1", "title": "Increase replicas"}],
        ),
        graph={"nodes": [{"id": resource_id, "short_id": short_id}]},
    )

    assert captured["resource_ids"] == [short_id]
    assert captured["recommendation_ids"] == ["rec-1"]