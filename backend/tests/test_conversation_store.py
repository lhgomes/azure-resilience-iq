from __future__ import annotations

from copy import deepcopy

from app.storage import conversation_store


def _stub_repository(monkeypatch, initial_payload):
    stored = deepcopy(initial_payload)

    monkeypatch.setattr(conversation_store, "read_json", lambda _path, default: deepcopy(stored))

    def write_payload(_path, payload):
        stored.clear()
        stored.update(deepcopy(payload))

    monkeypatch.setattr(conversation_store, "write_json", write_payload)
    return stored


def test_subscription_conversations_are_partitioned_by_flow(monkeypatch) -> None:
    stored = _stub_repository(monkeypatch, {"conversation_id": "legacy-shared-conversation"})

    assert conversation_store.get_subscription_conversation_id("sub-1", "chat") is None
    assert conversation_store.get_subscription_conversation_id("sub-1", "annotations") is None

    conversation_store.set_subscription_conversation_id("sub-1", "chat", "chat-conversation")
    conversation_store.set_subscription_conversation_id("sub-1", "annotations", "annotations-conversation")

    assert conversation_store.get_subscription_conversation_id("sub-1", "chat") == "chat-conversation"
    assert conversation_store.get_subscription_conversation_id("sub-1", "annotations") == "annotations-conversation"
    assert stored["conversation_id"] == "legacy-shared-conversation"

    conversation_store.set_subscription_conversation_id("sub-1", "chat", "")
    assert conversation_store.get_subscription_conversation_id("sub-1", "chat") is None
    assert conversation_store.get_subscription_conversation_id("sub-1", "annotations") == "annotations-conversation"


def test_context_seeded_state_is_partitioned_by_flow(monkeypatch) -> None:
    _stub_repository(monkeypatch, {"conversation_context_seeded": True})

    assert not conversation_store.is_workload_context_seeded("workload-1", "chat")

    conversation_store.set_workload_context_seeded("workload-1", "chat", True)

    assert conversation_store.is_workload_context_seeded("workload-1", "chat")
    assert not conversation_store.is_workload_context_seeded("workload-1", "terraform")


def test_context_fingerprint_is_partitioned_by_flow(monkeypatch) -> None:
    _stub_repository(monkeypatch, {})

    assert conversation_store.get_subscription_context_fingerprint("sub-1", "terraform") is None

    conversation_store.set_subscription_context_fingerprint("sub-1", "terraform", "fingerprint-1")
    conversation_store.set_subscription_context_fingerprint("sub-1", "chat", "fingerprint-2")

    assert conversation_store.get_subscription_context_fingerprint("sub-1", "terraform") == "fingerprint-1"
    assert conversation_store.get_subscription_context_fingerprint("sub-1", "chat") == "fingerprint-2"

    conversation_store.set_subscription_context_fingerprint("sub-1", "terraform", "")
    assert conversation_store.get_subscription_context_fingerprint("sub-1", "terraform") is None
    assert conversation_store.get_subscription_context_fingerprint("sub-1", "chat") == "fingerprint-2"