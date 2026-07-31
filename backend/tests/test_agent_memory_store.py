from __future__ import annotations

from copy import deepcopy

from app.storage import agent_memory_store


def _stub_repository(monkeypatch):
    stored = {}
    monkeypatch.setattr(agent_memory_store, "read_json", lambda _path, default: deepcopy(stored) or default)

    def write_payload(_path, payload):
        stored.clear()
        stored.update(deepcopy(payload))

    monkeypatch.setattr(agent_memory_store, "write_json", write_payload)
    return stored


def _append(flow: str, fingerprint: str) -> dict:
    return {
        "scope_type": "subscription",
        "scope_id": "sub-1",
        "flow": flow,
        "context_fingerprint": fingerprint,
        "user_intent": f"Intent from {flow}",
        "answer_summary": f"Answer from {flow}",
        "resource_ids": ["resource-1"],
        "recommendation_ids": ["recommendation-1"],
        "clarifying_questions": ["Question one?"],
    }


def test_handoffs_are_shared_across_flows_but_not_replayed_to_the_same_flow(monkeypatch) -> None:
    stored = _stub_repository(monkeypatch)
    agent_memory_store.append_cross_flow_handoff(**_append("chat", "fingerprint-1"))
    agent_memory_store.append_cross_flow_handoff(**_append("terraform", "fingerprint-1"))

    chat_handoffs = agent_memory_store.load_cross_flow_handoffs(
        "subscription", "sub-1", "chat", "fingerprint-1"
    )

    assert [item["flow"] for item in chat_handoffs] == ["terraform"]
    assert stored["schema_version"] == agent_memory_store.SCHEMA_VERSION
    assert stored["revision"] == 2


def test_context_change_invalidates_old_handoffs(monkeypatch) -> None:
    stored = _stub_repository(monkeypatch)
    agent_memory_store.append_cross_flow_handoff(**_append("chat", "old-fingerprint"))

    assert agent_memory_store.load_cross_flow_handoffs(
        "subscription", "sub-1", "terraform", "new-fingerprint"
    ) == []

    agent_memory_store.append_cross_flow_handoff(**_append("terraform", "new-fingerprint"))
    assert stored["revision"] == 1
    assert [item["flow"] for item in stored["handoffs"]] == ["terraform"]


def test_memory_is_bounded_and_compacted(monkeypatch) -> None:
    stored = _stub_repository(monkeypatch)
    for index in range(agent_memory_store.MAX_HANDOFFS + 2):
        values = _append(f"flow-{index}", "fingerprint-1")
        values["answer_summary"] = "word " * 500
        agent_memory_store.append_cross_flow_handoff(**values)

    assert len(stored["handoffs"]) == agent_memory_store.MAX_HANDOFFS
    assert len(stored["handoffs"][-1]["answer_summary"]) <= 1000


def test_latest_handoff_replaces_older_handoff_from_same_flow(monkeypatch) -> None:
    stored = _stub_repository(monkeypatch)
    agent_memory_store.append_cross_flow_handoff(**_append("chat", "fingerprint-1"))
    updated = _append("chat", "fingerprint-1")
    updated["answer_summary"] = "Latest answer"
    agent_memory_store.append_cross_flow_handoff(**updated)

    assert len(stored["handoffs"]) == 1
    assert stored["handoffs"][0]["answer_summary"] == "Latest answer"


def test_graph_fingerprint_changes_with_authoritative_context() -> None:
    graph = {
        "nodes": [{"id": "resource-1", "short_id": "short-1", "type": "storage"}],
        "edges": [],
        "resilience_evaluations": {"evaluations": {"resource-1": [{"status": "fail"}]}},
    }

    original = agent_memory_store.build_graph_context_fingerprint(graph)
    graph["resilience_evaluations"]["evaluations"]["resource-1"][0]["status"] = "pass"

    assert agent_memory_store.build_graph_context_fingerprint(graph) != original


def test_compact_resource_ids_maps_canonical_ids_and_preserves_unknown_values() -> None:
    canonical_id = "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Search/searchServices/search-1"
    short_id = "745d654a-38da-5b47-a7a2-2dbf9f4bddef"

    result = agent_memory_store.compact_resource_ids(
        [{"id": canonical_id, "short_id": short_id}],
        [canonical_id.upper(), "rec-or-resource-id"],
    )

    assert result == [short_id, "rec-or-resource-id"]


def test_compact_resource_ids_generates_uuid_when_node_has_no_short_id() -> None:
    canonical_id = "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Search/searchServices/search-1"

    result = agent_memory_store.compact_resource_ids([{"id": canonical_id}], [canonical_id])

    assert result == [agent_memory_store.short_id(canonical_id)]