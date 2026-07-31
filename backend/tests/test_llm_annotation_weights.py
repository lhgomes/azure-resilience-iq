from app.llm.annotator import _calculate_criticality_weights
from app.llm.models import LLMAnnotations, NodeAnnotation, NodeAnnotationPayload
from app.resilience.heuristic_validator import HeuristicValidator
from app.resilience.zonal_analyzer import ZonalAnalyzer
from app.resilience.zone_recommendation_engine import DeploymentPattern


def test_hidden_nodes_keep_criticality_weight() -> None:
    annotations = LLMAnnotations(
        nodes=[
            NodeAnnotation(
                node_id="storage",
                annotations=NodeAnnotationPayload(
                    criticality_score=2,
                    hide_by_default=True,
                ),
            ),
            NodeAnnotation(
                node_id="openai",
                annotations=NodeAnnotationPayload(
                    criticality_score=6,
                    hide_by_default=False,
                ),
            ),
        ]
    )

    weighted = _calculate_criticality_weights(annotations)

    assert weighted.nodes[0].annotations.criticality_weight == 25.0
    assert weighted.nodes[1].annotations.criticality_weight == 75.0
    assert sum(node.annotations.criticality_weight or 0 for node in weighted.nodes) == 100.0


def test_zrs_storage_redundancy_passes_without_llm() -> None:
    validator = object.__new__(HeuristicValidator)

    result = validator._check_obvious_failures(
        "microsoft.storage/storageaccounts",
        "Ensure that storage accounts are zone or region redundant",
        {"properties": {"account_replication_type": "ZRS"}},
    )

    assert result is not None
    assert result[0] == "pass"


def test_search_replicas_are_recognized_as_multi_zone() -> None:
    result = ZonalAnalyzer.extract_zonal_data({
        "type": "microsoft.search/searchservices",
        "location": "uaenorth",
        "properties": {"replica_count": 2},
    })

    assert result.deployment_pattern == DeploymentPattern.MULTI_ZONE
    assert result.zone_count == 2


def test_search_replica_recommendation_passes_without_llm() -> None:
    validator = object.__new__(HeuristicValidator)

    result = validator._check_obvious_failures(
        "microsoft.search/searchservices",
        "Enable AZ support in AI Search by configuring multiple replicas to your search service",
        {"properties": {"replica_count": 2}},
    )

    assert result is not None
    assert result[0] == "pass"


def test_traffic_manager_endpoint_is_not_independently_zonal() -> None:
    result = ZonalAnalyzer.extract_zonal_data({
        "type": "microsoft.network/trafficmanagerprofiles/endpoints",
        "properties": {},
    })

    assert result.deployment_pattern == DeploymentPattern.NOT_APPLICABLE


def test_openai_account_is_not_independently_zonal() -> None:
    result = ZonalAnalyzer.extract_zonal_data({
        "type": "microsoft.cognitiveservices/accounts",
        "location": "uaenorth",
        "properties": {"kind": "OpenAI"},
    })

    assert result.deployment_pattern == DeploymentPattern.NOT_APPLICABLE