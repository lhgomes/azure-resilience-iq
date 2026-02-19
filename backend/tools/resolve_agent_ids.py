import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

try:
    from app.settings import load_settings
except Exception:
    load_settings = None


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=ENV_PATH)


def _iter_assistants(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _find_by_name(assistants: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    target = (name or "").strip().lower()
    if not target:
        return None

    exact = [item for item in assistants if str(item.get("name", "")).strip().lower() == target]
    if exact:
        return exact[0]

    contains = [item for item in assistants if target in str(item.get("name", "")).strip().lower()]
    if len(contains) == 1:
        return contains[0]

    return None


def _require(value: Optional[str], label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError(f"Missing required value: {label}")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve Azure Foundry assistant IDs by assistant name via APIM."
    )
    parser.add_argument("--chat-name", default="resilience-iq-agent-chat")
    parser.add_argument("--resilience-name", default="resilience-iq-agent-resilience")
    parser.add_argument("--annotations-name", default="resilience-iq-agent-annotations")
    parser.add_argument("--base-url", default=os.getenv("AI_GATEWAY_AGENT_BASE_URL", ""))
    parser.add_argument(
        "--subscription-header-name",
        default=os.getenv("AI_GATEWAY_SUBSCRIPTION_HEADER_NAME", "api-key"),
    )
    parser.add_argument("--subscription-key", default=os.getenv("AI_GATEWAY_SUBSCRIPTION_KEY", ""))
    parser.add_argument("--show-all", action="store_true", help="Print all assistants returned by API")
    parser.add_argument(
        "--format",
        choices=["env", "json", "table"],
        default="env",
        help="Output format",
    )
    args = parser.parse_args()

    settings = load_settings() if load_settings else None
    ai_cfg = settings.get_ai_agent_config() if settings else {}

    try:
        base_url = _require(args.base_url or ai_cfg.get("gateway_base_url"), "AI gateway base URL")
        base_url = base_url.rstrip("/")
        subscription_key = _require(
            args.subscription_key or ai_cfg.get("subscription_key"),
            "AI gateway subscription key",
        )
        header_name = _require(
            args.subscription_header_name or ai_cfg.get("subscription_header_name"),
            "subscription header name",
        )
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    url = f"{base_url}/assistants"
    headers = {
        "Content-Type": "application/json",
        header_name: subscription_key,
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
    except Exception as error:
        print(f"ERROR: Request failed: {error}", file=sys.stderr)
        return 1

    if response.status_code >= 400:
        body = response.text[:1000]
        print(f"ERROR: HTTP {response.status_code} from {url}\n{body}", file=sys.stderr)
        return 1

    try:
        payload = response.json()
    except Exception:
        print("ERROR: API returned non-JSON payload.", file=sys.stderr)
        return 1

    assistants = _iter_assistants(payload)
    if not assistants:
        print("ERROR: No assistants found in API response.", file=sys.stderr)
        return 1

    chat = _find_by_name(assistants, args.chat_name)
    resilience = _find_by_name(assistants, args.resilience_name)
    annotations = _find_by_name(assistants, args.annotations_name)

    missing = []
    if not chat:
        missing.append(args.chat_name)
    if not resilience:
        missing.append(args.resilience_name)
    if not annotations:
        missing.append(args.annotations_name)

    result = {
        "AI_GATEWAY_CHAT_AGENT_ID": chat.get("id") if chat else None,
        "AI_GATEWAY_RESILIENCE_AGENT_ID": resilience.get("id") if resilience else None,
        "AI_GATEWAY_ANNOTATIONS_AGENT_ID": annotations.get("id") if annotations else None,
    }

    if args.format == "json":
        import json

        print(json.dumps(result, indent=2))
    elif args.format == "table":
        print("Flow         Name                             Agent ID")
        print("-----------  -------------------------------  --------------------------------")
        print(f"chat         {args.chat_name:<31}  {result['AI_GATEWAY_CHAT_AGENT_ID'] or '<not found>'}")
        print(f"resilience   {args.resilience_name:<31}  {result['AI_GATEWAY_RESILIENCE_AGENT_ID'] or '<not found>'}")
        print(f"annotations  {args.annotations_name:<31}  {result['AI_GATEWAY_ANNOTATIONS_AGENT_ID'] or '<not found>'}")
    else:
        for key, value in result.items():
            if value:
                print(f"{key}={value}")
            else:
                print(f"# {key}=<not found>")

    if args.show_all:
        print("\n# All assistants returned:")
        for item in assistants:
            print(f"- {item.get('name')} => {item.get('id')}")

    if missing:
        print("\nWARNING: Could not resolve these assistant names:", file=sys.stderr)
        for name in missing:
            print(f"- {name}", file=sys.stderr)
        return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
