#!/usr/bin/env python3

import argparse
import os
import sys

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv


def _require(value: str | None, name: str) -> str:
    if value and str(value).strip():
        return str(value).strip()
    raise ValueError(f"Missing required setting: {name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test Azure Foundry agent calls using latest SDK pattern")
    parser.add_argument("--env-file", default="backend/.env")
    parser.add_argument("--endpoint", default=None)
    parser.add_argument("--agent-name", default=None)
    parser.add_argument("--prompt", default="What is the size of France in square miles?")
    parser.add_argument("--follow-up", default="And what is the capital city?")
    parser.add_argument("--no-conversation", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    load_dotenv(args.env_file)

    endpoint = _require(args.endpoint or os.getenv("AI_FOUNDRY_PROJECT_ENDPOINT"), "AI_FOUNDRY_PROJECT_ENDPOINT")
    agent_name = _require(args.agent_name or os.getenv("AI_FOUNDRY_CHAT_AGENT_REFERENCE"), "AI_FOUNDRY_CHAT_AGENT_REFERENCE")
    api_version = str(
        os.getenv("AI_FOUNDRY_OPENAI_API_VERSION")
        or os.getenv("OPENAI_API_VERSION")
        or ""
    ).strip()
    if api_version:
        os.environ["OPENAI_API_VERSION"] = api_version

    project_client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
    openai_client = project_client.get_openai_client()

    conversation_id = None
    if not args.no_conversation:
        conversation = openai_client.conversations.create()
        conversation_id = conversation.id
        print(f"Created conversation (id: {conversation_id})")

    request_args = {
        "extra_body": {"agent_reference": {"name": agent_name, "type": "agent_reference"}},
        "input": args.prompt,
    }
    if conversation_id:
        request_args["conversation"] = conversation_id

    response = openai_client.responses.create(**request_args)
    print(f"First response: {getattr(response, 'output_text', '')}")

    if args.follow_up:
        follow_up_args = {
            "extra_body": {"agent_reference": {"name": agent_name, "type": "agent_reference"}},
            "input": args.follow_up,
        }
        if conversation_id:
            follow_up_args["conversation"] = conversation_id

        follow_up_response = openai_client.responses.create(**follow_up_args)
        print(f"Follow-up response: {getattr(follow_up_response, 'output_text', '')}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
