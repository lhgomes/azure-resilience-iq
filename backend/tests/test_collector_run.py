from app.collector.run import merge_collector_output


def test_collector_output_preserves_app_owned_metadata() -> None:
    existing = {
        "subscription_id": "sub-1",
        "subscription_name": "Old name",
        "resources": [{"id": "old"}],
        "role_assignments": [{"id": "old-role"}],
        "conversation_ids": {"chat": "conv-chat", "terraform": "conv-terraform"},
        "conversation_context_fingerprints": {"chat": "fingerprint"},
        "conversation_context_seeded_by_flow": {"chat": True},
    }
    collected = {
        "subscription_id": "sub-1",
        "subscription_name": "Current name",
        "resources": [{"id": "current"}],
        "role_assignments": [{"id": "current-role"}],
    }

    merged = merge_collector_output(existing, collected)

    assert merged["subscription_name"] == "Current name"
    assert merged["resources"] == [{"id": "current"}]
    assert merged["role_assignments"] == [{"id": "current-role"}]
    assert merged["conversation_ids"] == existing["conversation_ids"]
    assert merged["conversation_context_fingerprints"] == existing["conversation_context_fingerprints"]
    assert merged["conversation_context_seeded_by_flow"] == existing["conversation_context_seeded_by_flow"]