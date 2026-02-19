import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterable, List
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from dotenv import load_dotenv
import requests

try:
    from app.settings import load_settings
except Exception:
    load_settings = None


# Auto-load backend/.env when script runs directly from tools/
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=ENV_PATH)


class HttpRequestError(RuntimeError):
    def __init__(self, status_code: int, method: str, url: str, body: str):
        self.status_code = int(status_code)
        self.method = method
        self.url = url
        self.body = body
        super().__init__(f"HTTP {status_code} for {method} {url}: {body}")


def _redact_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url

    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    redacted = []
    for key, value in pairs:
        if key.lower() in {"subscription-key", "api-key", "ocp-apim-subscription-key"}:
            redacted.append((key, "***"))
        else:
            redacted.append((key, value))

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(redacted),
            parsed.fragment,
        )
    )


def _iter_messages(messages: Any) -> List[Any]:
    if isinstance(messages, dict):
        data = messages.get("data")
        if isinstance(data, list):
            return data
    if hasattr(messages, "data") and isinstance(messages.data, list):
        return messages.data
    try:
        return list(messages)
    except Exception:
        return []


def _extract_text(message: Any) -> str:
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                text_obj = item.get("text")
                if isinstance(text_obj, dict) and text_obj.get("value"):
                    parts.append(str(text_obj["value"]))
            else:
                text_obj = getattr(item, "text", None)
                value = getattr(text_obj, "value", None) if text_obj else None
                if value:
                    parts.append(str(value))
        return "\n".join(parts).strip()

    return ""


def _role_value(message: Any) -> str:
    if isinstance(message, dict):
        role = str(message.get("role", "")).lower()
    else:
        role = str(getattr(message, "role", "")).lower()
    if "." in role:
        role = role.split(".")[-1]
    return role


def _find_latest_agent_text(messages: Iterable[Any]) -> str:
    for message in messages:
        role = _role_value(message)
        if role not in {"agent", "assistant"}:
            continue
        text = _extract_text(message)
        if text:
            return text
    return ""


def _http(
    session: requests.Session,
    method: str,
    url: str,
    *,
    headers: dict,
    payload: Any = None,
    timeout: float = 30.0,
) -> dict:
    response = session.request(method=method, url=url, headers=headers, json=payload, timeout=timeout)
    if response.status_code >= 400:
        body = response.text[:1500]
        raise HttpRequestError(response.status_code, method, _redact_url(url), body)
    if not response.text:
        return {}
    try:
        return response.json()
    except Exception as exc:
        raise RuntimeError(f"Invalid JSON response from {method} {url}: {response.text[:500]}") from exc


def _poll_run(
    session: requests.Session,
    *,
    run_url: str,
    headers: dict,
    timeout_seconds: int,
    interval_seconds: float,
) -> dict:
    deadline = time.time() + float(timeout_seconds)
    while True:
        run_state = _http(session, "GET", run_url, headers=headers)
        status = str(run_state.get("status", "")).lower()

        if status in {"completed", "failed", "cancelled", "expired"}:
            return run_state

        if time.time() >= deadline:
            raise RuntimeError(f"Timed out waiting for run completion. Last status: {status}")

        time.sleep(interval_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Azure Foundry agent request/response path")
    parser.add_argument("--question", default="Summarize your purpose in 2 sentences.")
    parser.add_argument("--require-json", action="store_true")
    parser.add_argument("--must-contain", action="append", default=[])
    parser.add_argument("--base-url", default=os.getenv("AI_GATEWAY_AGENT_BASE_URL", ""))
    parser.add_argument("--subscription-key-header", default=os.getenv("AI_GATEWAY_SUBSCRIPTION_HEADER_NAME", "api-key"))
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--poll-interval", type=float, default=1.5)
    args = parser.parse_args()

    settings = load_settings() if load_settings else None
    ai_agent_cfg = settings.get_ai_agent_config() if settings else {}

    gateway_base = os.getenv("AI_GATEWAY_ENDPOINT") or ai_agent_cfg.get("gateway_base_url")
    subscription_key = os.getenv("AI_GATEWAY_SUBSCRIPTION_KEY")
    configured_agent = os.getenv("AI_GATEWAY_AGENT_ID") or ai_agent_cfg.get("agent_id")

    if not configured_agent:
        print("ERROR: AI_GATEWAY_AGENT_ID or ai_agent.agent_id is required", file=sys.stderr)
        return 2
    if not gateway_base:
        print("ERROR: AI_GATEWAY_ENDPOINT or ai_agent.gateway_base_url is required", file=sys.stderr)
        return 2
    if not subscription_key:
        print("ERROR: AI_GATEWAY_SUBSCRIPTION_KEY is required", file=sys.stderr)
        return 2

    if not configured_agent.strip().startswith("asst"):
        print(
            "ERROR: agent id must be an exact asst_* id when using APIM REST path.",
            file=sys.stderr,
        )
        return 2

    configured_base_url = (args.base_url or "").strip().rstrip("/")
    if not configured_base_url and str(ai_agent_cfg.get("gateway_base_url") or "").strip():
        configured_base_url = str(ai_agent_cfg.get("gateway_base_url")).strip().rstrip("/")
    if not configured_base_url:
        configured_base_url = str(gateway_base).strip().rstrip("/")
    base_candidates = [configured_base_url] if configured_base_url else []
    if not base_candidates:
        print("ERROR: Missing APIM base URL. Set AI_GATEWAY_AGENT_BASE_URL or --base-url.", file=sys.stderr)
        return 2

    agent_id = configured_agent.strip()
    print(f"Using agent_id={agent_id}")

    common_headers = {"Content-Type": "application/json"}
    if "--subscription-key-header" not in sys.argv and ai_agent_cfg.get("subscription_header_name"):
        args.subscription_key_header = str(ai_agent_cfg.get("subscription_header_name"))

    header_name = (args.subscription_key_header or "").strip()
    if header_name:
        common_headers[header_name] = subscription_key

    session = requests.Session()

    selected_base = ""
    thread = {}
    thread_url = ""
    for candidate in base_candidates:
        attempt_url = f"{candidate}/threads"
        try:
            thread = _http(session, "POST", attempt_url, headers=common_headers, payload={})
            selected_base = candidate
            thread_url = attempt_url
            break
        except HttpRequestError as error:
            if error.status_code == 404:
                continue
            raise

    if not selected_base:
        print("ERROR: APIM route not found for any candidate base URL.", file=sys.stderr)
        print("Tried:", file=sys.stderr)
        for candidate in base_candidates:
            print(f"  - {_redact_url(f'{candidate}/threads')}", file=sys.stderr)
        print(
            "Set AI_GATEWAY_AGENT_BASE_URL to your exact APIM API base path (for example https://...azure-api.net/<api-suffix>).",
            file=sys.stderr,
        )
        return 1

    print(f"Using gateway_base={selected_base}")

    thread_id = thread.get("id")
    if not thread_id:
        print("ERROR: Thread creation did not return thread id", file=sys.stderr)
        return 1

    message_url = f"{selected_base}/threads/{thread_id}/messages"
    _http(
        session,
        "POST",
        message_url,
        headers=common_headers,
        payload={"role": "user", "content": args.question},
    )

    run_create_url = f"{selected_base}/threads/{thread_id}/runs"
    run = _http(
        session,
        "POST",
        run_create_url,
        headers=common_headers,
        payload={"assistant_id": agent_id},
    )
    run_id = run.get("id")
    if not run_id:
        print("ERROR: Run creation did not return run id", file=sys.stderr)
        return 1

    run_get_url = f"{selected_base}/threads/{thread_id}/runs/{run_id}"
    run_state = _poll_run(
        session,
        run_url=run_get_url,
        headers=common_headers,
        timeout_seconds=args.timeout_seconds,
        interval_seconds=args.poll_interval,
    )

    status = str(run_state.get("status", ""))
    print(f"run_status={status}")
    if "failed" in status.lower():
        print(f"ERROR: run failed: {run_state.get('last_error')}", file=sys.stderr)
        return 1

    messages_url = f"{selected_base}/threads/{thread_id}/messages"
    messages_payload = _http(session, "GET", messages_url, headers=common_headers)
    messages = _iter_messages(messages_payload)
    answer = _find_latest_agent_text(messages)
    if not answer:
        print("ERROR: No agent text found in thread messages", file=sys.stderr)
        return 1

    if args.require_json:
        try:
            json.loads(answer)
        except Exception:
            print("ERROR: Response is not valid JSON", file=sys.stderr)
            print(answer)
            return 1

    lowered = answer.lower()
    for token in args.must_contain:
        if token.lower() not in lowered:
            print(f"ERROR: Response missing expected token: {token}", file=sys.stderr)
            print(answer)
            return 1

    print("OK: Agent request succeeded and response passed checks")
    print("response_preview=")
    print(answer[:1200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
