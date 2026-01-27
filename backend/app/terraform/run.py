"""
Terraform input collector - alternative to Azure Resource Graph collector.

Processes Terraform .tf files and generates resources.json and edges.json,
allowing users to analyze Terraform-defined infrastructure without needing
Azure credentials or an active subscription.

Usage:
  python -m app.terraform.run --terraform-dir ./backend/sample/terraform-3tier
  python -m app.terraform.run --terraform-file main.tf --subscription-id my-sub-uuid
  python -m app.terraform.run --terraform-json state.json --use-state-json
"""

import json
import argparse
import uuid as uuid_module
from pathlib import Path
from typing import Optional

from app.config import get_subscription_dir, get_resources_path, get_edges_path
from app.resource_filters import load_monitored_resource_types
from app.terraform.parser import TerraformParser
from app.terraform.generator import TerraformResourceGenerator


def main():
    parser = argparse.ArgumentParser(
        description="Terraform input collector for azure-workload-graph",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process entire Terraform directory
  python -m app.terraform.run --terraform-dir ./backend/sample/terraform-3tier

  # Process single file with custom subscription ID
  python -m app.terraform.run --terraform-file main.tf --subscription-id 12345678-1234-1234-1234-123456789abc

  # Process Terraform state JSON
  python -m app.terraform.run --terraform-json terraform.tfstate --use-state-json
        """
    )
    
    # Input source (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--terraform-dir",
        type=str,
        help="Path to directory containing .tf files"
    )
    input_group.add_argument(
        "--terraform-file",
        type=str,
        help="Path to single .tf file"
    )
    input_group.add_argument(
        "--terraform-json",
        type=str,
        help="Path to terraform.tfstate or terraform show -json output"
    )
    
    # Options
    parser.add_argument(
        "--subscription-id",
        type=str,
        default=None,
        help="Subscription ID (UUID). Auto-generated if not provided."
    )
    parser.add_argument(
        "--subscription-name",
        type=str,
        default=None,
        help="Friendly name for subscription (default: input folder/file name)"
    )
    parser.add_argument(
        "--use-state-json",
        action="store_true",
        help="Parse input as Terraform JSON state (terraform show -json)"
    )
    parser.add_argument(
        "--var",
        action="append",
        dest="variables",
        help="Set variable value (format: name=value). Can be used multiple times."
    )
    
    args = parser.parse_args()
    
    # Generate subscription ID if not provided
    if not args.subscription_id:
        # Generate UUID based on folder/file name for reproducibility
        if args.terraform_dir:
            seed_name = Path(args.terraform_dir).name
        elif args.terraform_file:
            seed_name = Path(args.terraform_file).stem
        elif args.terraform_json:
            seed_name = Path(args.terraform_json).stem
        else:
            seed_name = "terraform"
        
        # Create a deterministic UUID from the folder name
        import hashlib
        hash_bytes = hashlib.sha1(seed_name.encode()).digest()
        args.subscription_id = str(uuid_module.UUID(bytes=hash_bytes[:16]))
        print(f"📝 Generated subscription ID from '{seed_name}': {args.subscription_id}")
    
    # Derive subscription name from input source if not provided
    if not args.subscription_name:
        if args.terraform_dir:
            args.subscription_name = Path(args.terraform_dir).name
        elif args.terraform_file:
            args.subscription_name = Path(args.terraform_file).stem
        elif args.terraform_json:
            args.subscription_name = Path(args.terraform_json).stem
        else:
            args.subscription_name = args.subscription_id
    
    # Validate subscription ID format (UUID)
    try:
        uuid_module.UUID(args.subscription_id)
    except ValueError:
        print(f"❌ Invalid subscription ID format. Must be a valid UUID.")
        return 1
    
    print(f"🔄 Processing Terraform configuration...")
    
    # Parse user-provided variables
    user_variables = {}
    if hasattr(args, 'variables') and args.variables:
        for var_assignment in args.variables:
            if '=' in var_assignment:
                var_name, var_value = var_assignment.split('=', 1)
                # Try to parse as number or boolean
                if var_value.lower() == 'true':
                    user_variables[var_name] = True
                elif var_value.lower() == 'false':
                    user_variables[var_name] = False
                elif var_value.isdigit():
                    user_variables[var_name] = int(var_value)
                else:
                    try:
                        user_variables[var_name] = float(var_value)
                    except ValueError:
                        user_variables[var_name] = var_value
        
        if user_variables:
            print(f"📝 Using {len(user_variables)} user-provided variable(s)")
    
    allowed_types = load_monitored_resource_types()

    try:
        # Parse Terraform
        tf_parser = TerraformParser()
        
        if args.terraform_dir:
            print(f"📁 Scanning directory: {args.terraform_dir}")
            tf_resources = tf_parser.parse_directory(Path(args.terraform_dir), user_variables)
        
        elif args.terraform_file:
            print(f"📄 Parsing file: {args.terraform_file}")
            tf_resources = tf_parser.parse_file(Path(args.terraform_file))
            # Apply resolution for single file too
            tf_parser.resolve_variables_and_locals(user_variables)
            tf_parser.apply_resolutions_to_resources()
        
        elif args.terraform_json:
            print(f"📊 Parsing Terraform JSON: {args.terraform_json}")
            json_data = json.loads(Path(args.terraform_json).read_text())
            tf_resources = tf_parser.parse_json_state(json_data)
        
        print(f"✓ Parsed {len(tf_resources)} Terraform resources")
        
        if not tf_resources:
            print("⚠️  No resources found in Terraform files")
            return 1
        
        # Generate resources and edges
        print(f"🔨 Generating standard format...")
        generator = TerraformResourceGenerator(
            subscription_id=args.subscription_id,
            subscription_name=args.subscription_name
        )
        generator.add_resources(tf_resources)
        resources_output, edges_output = generator.generate()

        monitored_types = allowed_types or set()
        for res in resources_output["resources"]:
            res_type = str(res.get("type", "")).lower()
            res["monitored"] = res_type in monitored_types

        if allowed_types:
            monitored_count = sum(1 for r in resources_output["resources"] if r.get("monitored"))
            print(f"ℹ️ Flagged {monitored_count}/{len(resources_output['resources'])} resources as monitored types")
        
        # Create subscription directory
        sub_dir = get_subscription_dir(args.subscription_id)
        sub_dir.mkdir(parents=True, exist_ok=True)
        
        # Save resources
        resources_path = get_resources_path(args.subscription_id)
        resources_path.write_text(json.dumps(resources_output, indent=2))
        print(f"✓ Saved {len(resources_output['resources'])} resources to {resources_path}")
        
        # Save edges (manual edges are kept separate and merged at read time)
        edges_path = get_edges_path(args.subscription_id)
        edges_path.write_text(json.dumps(edges_output, indent=2))
        print(f"✓ Saved {len(edges_output['edges'])} edges to {edges_path}")
        
        # Summary
        print(f"\n✅ Terraform collection complete!")
        print(f"   Subscription: {args.subscription_id}")
        print(f"   Name: {args.subscription_name}")
        print(f"   Resources: {len(resources_output['resources'])}")
        print(f"   Relationships: {len(edges_output['edges'])}")
        print(f"\n💡 Next steps:")
        print(f"   1. Run resilience evaluation:")
        print(f"      python -m app.resilience.run --subscription-id {args.subscription_id}")
        print(f"   2. Run LLM annotations (optional):")
        print(f"      python -m app.llm.run --subscription-id {args.subscription_id}")
        print(f"   3. Start API server:")
        print(f"      uvicorn app.main:app --reload")
        
        return 0
    
    except FileNotFoundError as e:
        print(f"❌ File not found: {e}")
        return 1
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON: {e}")
        return 1
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
