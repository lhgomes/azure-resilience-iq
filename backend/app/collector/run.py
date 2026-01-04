import json
import argparse
from typing import List

from azure.identity import AzureCliCredential
from .arg import query_resources

from app.config import COLLECTOR_DIR


OUTPUT_DIR = COLLECTOR_DIR
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_current_subscription() -> str:
    """
    Uses Azure CLI context.
    """
    credential = AzureCliCredential()
    token = credential.get_token("https://management.azure.com/.default")
    # Subscription is resolved by ARG using CLI context
    return None


def main():
    parser = argparse.ArgumentParser(description="Azure ARG Collector")
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument("--resource-group", action="append")
    parser.add_argument("--tag", action="append", help="key=value")

    args = parser.parse_args()

    tags = None
    if args.tag:
        tags = dict(t.split("=", 1) for t in args.tag)

    resources = query_resources(
        subscription_id=args.subscription_id,
        resource_groups=args.resource_group,
        tags=tags,
    )

    output = [r.model_dump() for r in resources]

    out_file = OUTPUT_DIR / "resources.json"
    out_file.write_text(json.dumps(output, indent=2))

    print(f"✔ Collected {len(resources)} resources")
    print(f"✔ Written to {out_file}")


if __name__ == "__main__":
    main()
