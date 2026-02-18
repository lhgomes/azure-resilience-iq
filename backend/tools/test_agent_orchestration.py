import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple
from urllib.parse import urlencode

import requests


REQUIRED_RESPONSE_KEYS = [
    "message",
    "sources",
    "suggested_edges",
    "resources_to_highlight",
    "criticality_insights",
    "recommendations",
    "remediation_guide",
    "terraform_code",
    "clarifying_questions",
]


@dataclass
class TestCase:
    name: str
    message: str
    context: Dict[str, Any]


def _request_json(method: str, url: str, timeout: float, payload: Dict[str, Any] | None = None) -> Tuple[int, Dict[str, Any] | None, str]:
    try:
        response = requests.request(method=method, url=url, json=payload, timeout=timeout)
    except Exception as error:
        return 0, None, f"request error: {error}"

    body_text = response.text
    try:
        body_json = response.json()
    except Exception:
        body_json = None

    return response.status_code, body_json, body_text


def _validate_top_level_contract(response: Dict[str, Any]) -> List[str]:
    errors: List[str] = []

    for key in REQUIRED_RESPONSE_KEYS:
        if key not in response:
            errors.append(f"missing required key: {key}")

    if not isinstance(response.get("message"), str) or not response.get("message", "").strip():
        errors.append("message must be a non-empty string")

    for list_key in [
        "sources",
        "suggested_edges",
        "resources_to_highlight",
        "criticality_insights",
        "recommendations",
        "clarifying_questions",
    ]:
        if list_key in response and not isinstance(response.get(list_key), list):
            errors.append(f"{list_key} must be a list")

    return errors


def _validate_sources(response: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    sources = response.get("sources", [])

    for idx, source in enumerate(sources):
        if not isinstance(source, dict):
            errors.append(f"sources[{idx}] must be an object")
            continue

        if "url" in source:
            url = str(source.get("url") or "").strip()
            if url and not (url.startswith("http://") or url.startswith("https://")):
                errors.append(f"sources[{idx}].url must be absolute http(s) URL when provided")

    return errors


def _validate_flow_expectations(case_name: str, response: Dict[str, Any]) -> List[str]:
    errors: List[str] = []

    if case_name == "annotations":
        has_annotation_payload = any(
            len(response.get(key, [])) > 0
            for key in ["suggested_edges", "resources_to_highlight", "criticality_insights"]
        )
        has_clarification = len(response.get("clarifying_questions", [])) > 0
        if not has_annotation_payload and not has_clarification:
            errors.append(
                "annotations flow should return graph annotations or clarifying_questions"
            )

    if case_name == "resilience":
        has_recommendations = len(response.get("recommendations", [])) > 0
        has_clarification = len(response.get("clarifying_questions", [])) > 0
        if not has_recommendations and not has_clarification:
            errors.append(
                "resilience flow should return recommendations or clarifying_questions"
            )

    return errors


def _validate_rag_trace(case_name: str, response: Dict[str, Any], validate_rag: bool) -> List[str]:
    if not validate_rag:
        return []

    errors: List[str] = []
    rag_trace = response.get("rag_trace")
    if not isinstance(rag_trace, dict):
        return ["rag_trace must be included as an object when --validate-rag is enabled"]

    for key in ["flow", "rag_expected", "rag_used", "source_type_counts", "validation_passed"]:
        if key not in rag_trace:
            errors.append(f"rag_trace missing key: {key}")

    flow = str(rag_trace.get("flow") or "")
    rag_expected = bool(rag_trace.get("rag_expected"))
    rag_used = bool(rag_trace.get("rag_used"))
    source_type_counts = rag_trace.get("source_type_counts") or {}

    if case_name == "resilience":
        if flow != "resilience":
            errors.append(f"resilience probe routed to unexpected flow: {flow}")
        if not rag_expected:
            errors.append("resilience flow must set rag_expected=true")
        if not rag_used:
            errors.append("resilience flow should indicate rag_used=true")
        external_count = int(source_type_counts.get("APRL", 0)) + int(source_type_counts.get("MicrosoftLearn", 0))
        if external_count <= 0:
            errors.append("resilience flow should include APRL or MicrosoftLearn sources")

    if case_name == "annotations":
        if flow != "annotations":
            errors.append(f"annotations probe routed to unexpected flow: {flow}")
        if rag_expected:
            errors.append("annotations flow must set rag_expected=false")
        if rag_used:
            errors.append("annotations flow should not indicate rag_used=true")

    if case_name == "chat":
        if flow != "chat":
            errors.append(f"chat probe routed to unexpected flow: {flow}")

    return errors


def run_test_case(
    base_url: str,
    subscription_id: str,
    case: TestCase,
    timeout: float,
    show_body: bool,
    include_rag_trace: bool,
    validate_rag: bool,
) -> Tuple[bool, List[str], Dict[str, Any] | None]:
    query_string = urlencode({"include_rag_trace": str(include_rag_trace).lower()}) if include_rag_trace else ""
    url = f"{base_url}/api/subscriptions/{subscription_id}/chat"
    if query_string:
        url = f"{url}?{query_string}"
    payload = {
        "message": case.message,
        "subscription_id": subscription_id,
        "context": case.context,
    }

    status, body_json, body_text = _request_json("POST", url, timeout=timeout, payload=payload)

    errors: List[str] = []
    if status != 200:
        errors.append(f"HTTP {status} from {url}")
        if body_text:
            errors.append(f"response body: {body_text[:500]}")
        return False, errors, body_json

    if not isinstance(body_json, dict):
        errors.append("response is not a JSON object")
        if body_text:
            errors.append(f"raw response: {body_text[:500]}")
        return False, errors, None

    errors.extend(_validate_top_level_contract(body_json))
    errors.extend(_validate_sources(body_json))
    errors.extend(_validate_flow_expectations(case.name, body_json))
    errors.extend(_validate_rag_trace(case.name, body_json, validate_rag))

    if show_body:
        print(json.dumps(body_json, indent=2)[:3000])

    return len(errors) == 0, errors, body_json


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test and validate agent orchestration (chat/annotations/resilience) via API."
    )
    parser.add_argument("--subscription-id", required=True, help="Azure subscription id to test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend API base URL")
    parser.add_argument("--timeout", type=float, default=90.0, help="Request timeout in seconds")
    parser.add_argument("--show-body", action="store_true", help="Print response payloads")
    parser.add_argument("--save-results", default="", help="Optional path to save full test results as JSON")
    parser.add_argument("--validate-rag", action="store_true", help="Assert debug rag_trace behavior per flow")
    parser.add_argument("--include-rag-trace", action="store_true", help="Request rag_trace in responses")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    subscription_id = args.subscription_id.strip()
    include_rag_trace = args.include_rag_trace or args.validate_rag

    print("[1/2] Checking chat availability...")
    availability_url = f"{base_url}/api/chat/availability"
    status, body_json, body_text = _request_json("GET", availability_url, timeout=args.timeout)

    if status != 200:
        print(f"FAIL: availability endpoint returned HTTP {status}")
        print(body_text[:500])
        return 1

    if not isinstance(body_json, dict):
        print("FAIL: availability endpoint did not return JSON object")
        return 1

    if not body_json.get("available"):
        print("FAIL: chat feature is not available")
        print(f"Reason: {body_json.get('reason')}")
        return 1

    print("PASS: chat availability is true")

    test_cases = [
        TestCase(
            name="chat",
            message="Summarize this workload architecture at a high level.",
            context={"tab": "overview"},
        ),
        TestCase(
            name="annotations",
            message="What dependencies or relationships are missing in this graph?",
            context={"tab": "connections"},
        ),
        TestCase(
            name="resilience",
            message="Give me the top resilience gaps and remediation priorities.",
            context={"tab": "findings"},
        ),
    ]

    print("[2/2] Running orchestration probes...")
    results: Dict[str, Any] = {
        "base_url": base_url,
        "subscription_id": subscription_id,
        "availability": body_json,
        "tests": [],
    }

    passed = 0
    failed = 0

    for case in test_cases:
        print(f"\n--- Test: {case.name} ---")
        ok, errors, response = run_test_case(
            base_url=base_url,
            subscription_id=subscription_id,
            case=case,
            timeout=args.timeout,
            show_body=args.show_body,
            include_rag_trace=include_rag_trace,
            validate_rag=args.validate_rag,
        )

        test_result = {
            "name": case.name,
            "ok": ok,
            "errors": errors,
            "response": response,
        }
        results["tests"].append(test_result)

        if ok:
            passed += 1
            print("PASS")
            if isinstance(response, dict):
                print(f"message_preview: {str(response.get('message', ''))[:120]}")
                print(
                    "counts: "
                    f"sources={len(response.get('sources', []))}, "
                    f"suggested_edges={len(response.get('suggested_edges', []))}, "
                    f"recommendations={len(response.get('recommendations', []))}, "
                    f"clarifying_questions={len(response.get('clarifying_questions', []))}"
                )
        else:
            failed += 1
            print("FAIL")
            for error in errors:
                print(f"- {error}")

    if args.save_results:
        try:
            with open(args.save_results, "w", encoding="utf-8") as handle:
                json.dump(results, handle, indent=2, ensure_ascii=False)
            print(f"\nSaved results to: {args.save_results}")
        except Exception as error:
            print(f"WARNING: could not save results file: {error}")

    print("\n=== Summary ===")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")

    if failed > 0:
        print("\nTip: For deeper proof, correlate rag_trace.trace_id with APIM logs for /threads/*/runs payload assistant_id.")
        return 1

    print("All orchestration tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
