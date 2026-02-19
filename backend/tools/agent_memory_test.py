import argparse
import random
import string
import sys

from dotenv import load_dotenv

from app.settings import load_settings
from app.llm.gateway import create_llm_gateway


SYSTEM_PROMPT = (
    "You are a memory validation assistant. "
    "Rules: respond with ONLY the requested token value or the exact word NONE. "
    "No extra text."
)


def _random_marker(prefix: str) -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"{prefix}-{suffix}"


def _ask(gateway, prompt: str, model: str | None, conversation_id: str | None = None) -> tuple[str, str | None]:
    response = gateway.generate_text(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=prompt,
        temperature=0.0,
        max_tokens=64,
        model=model,
        conversation_id=conversation_id,
    ).strip()
    metrics = gateway.get_last_metrics()
    generated_conversation_id = metrics.get("conversation_id") if isinstance(metrics, dict) else None
    return response, generated_conversation_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Foundry memory isolation by subscription/workload keys")
    parser.add_argument("--sub-a", default="sub:A")
    parser.add_argument("--sub-b", default="sub:B")
    parser.add_argument("--wl-a", default="wl:001")
    parser.add_argument("--wl-b", default="wl:002")
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    load_dotenv(".env")
    settings = load_settings()
    gateway = create_llm_gateway(settings)

    if not gateway.is_available():
        print("ERROR: Gateway unavailable", file=sys.stderr)
        return 2

    marker_a = _random_marker("ALPHA")
    marker_b = _random_marker("BRAVO")

    key_a = f"{args.sub_a}|{args.wl_a}"
    key_b = f"{args.sub_b}|{args.wl_b}"

    print(f"Testing conversation stream A: {key_a}")
    print(f"Testing conversation stream B: {key_b}")

    conversation_a = None
    conversation_b = None

    # Seed memory A
    ack_a, conversation_a = _ask(
        gateway,
        f"Remember marker {marker_a}. Reply only {marker_a}",
        args.model,
        conversation_a,
    )
    if marker_a not in ack_a:
        print(f"ERROR: Failed to seed key A memory. Response: {ack_a}", file=sys.stderr)
        return 1

    # Recall memory A
    recall_a, conversation_a = _ask(
        gateway,
        "What marker is remembered? Reply marker or NONE",
        args.model,
        conversation_a,
    )
    if marker_a not in recall_a:
        print(f"ERROR: Key A did not recall its marker. Response: {recall_a}", file=sys.stderr)
        return 1

    # Key B should not know marker A
    recall_b_before, conversation_b = _ask(
        gateway,
        "What marker is remembered? Reply marker or NONE",
        args.model,
        conversation_b,
    )
    if marker_a in recall_b_before:
        print(f"ERROR: Memory bleed detected: key B saw marker A. Response: {recall_b_before}", file=sys.stderr)
        return 1

    # Seed and recall key B
    ack_b, conversation_b = _ask(
        gateway,
        f"Remember marker {marker_b}. Reply only {marker_b}",
        args.model,
        conversation_b,
    )
    if marker_b not in ack_b:
        print(f"ERROR: Failed to seed key B memory. Response: {ack_b}", file=sys.stderr)
        return 1

    recall_b, conversation_b = _ask(
        gateway,
        "What marker is remembered? Reply marker or NONE",
        args.model,
        conversation_b,
    )
    if marker_b not in recall_b:
        print(f"ERROR: Key B did not recall its marker. Response: {recall_b}", file=sys.stderr)
        return 1

    # Ensure key A still isolated
    recall_a_after, conversation_a = _ask(
        gateway,
        "What marker is remembered? Reply marker or NONE",
        args.model,
        conversation_a,
    )
    if marker_b in recall_a_after:
        print(f"ERROR: Memory bleed detected: key A saw marker B. Response: {recall_a_after}", file=sys.stderr)
        return 1

    print("OK: Memory isolation validated")
    print(f"A recall: {recall_a_after}")
    print(f"B recall: {recall_b}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
