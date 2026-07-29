from app.chat.service import ChatService


def _service_without_dependencies() -> ChatService:
    return ChatService.__new__(ChatService)


def test_capability_questions_have_a_distinct_query_type():
    service = _service_without_dependencies()

    assert service._detect_query_type("What can you help with?") == "capabilities"
    assert service._detect_query_type("How can you help me?") == "capabilities"
    assert service._detect_query_type("What are your capabilities?") == "capabilities"


def test_action_requests_are_not_misclassified_as_capability_questions():
    service = _service_without_dependencies()

    assert service._detect_query_type("Can you help me remediate this finding?") == "remediation"
    assert service._detect_query_type("How can you help fix this resource?") == "remediation"


def test_capability_prompt_omits_workload_context_and_clarifications():
    service = _service_without_dependencies()

    prompt = service._build_prompt(
        query="What can you help with?",
        graph={
            "nodes": [{"id": "resource-1"}],
            "edges": [],
            "evaluations": [{"status": "fail", "recommendation_id": "rec-1"}],
        },
        subscription_id="subscription-1",
        context=None,
        query_type="capabilities",
        referenced_resource_ids=None,
        include_full_context=True,
    )

    assert "CAPABILITY DISCOVERY MODE" in prompt
    assert "Do not analyze the current workload" in prompt
    assert "WORKLOAD CONTEXT" not in prompt
    assert "Failed findings" not in prompt
    assert "resource-1" not in prompt
    assert "rec-1" not in prompt


def test_capability_response_discards_recommendations_and_clarifications():
    service = _service_without_dependencies()

    response = service._process_llm_response(
        llm_output={
            "message": "I can help assess and improve Azure workload resilience.",
            "recommendations": [{"recommendation_id": "rec-1", "title": "Unrequested finding"}],
            "clarifying_questions": [
                {
                    "question": "Which workflow do you want?",
                    "possible_answers": ["Assess resilience"],
                }
            ],
        },
        graph={"nodes": [], "edges": []},
        query="What can you help with?",
        query_type="capabilities",
        flow="chat",
        include_rag_trace=False,
        target_agent_id="chat-agent",
        scope_type="subscription",
        scope_id="subscription-1",
        conversation_id=None,
        trace_id="trace-1",
    )

    assert response.message == "I can help assess and improve Azure workload resilience."
    assert response.recommendations == []
    assert response.clarifying_questions == []