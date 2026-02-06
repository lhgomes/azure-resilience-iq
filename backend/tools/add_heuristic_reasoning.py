#!/usr/bin/env python3
"""
Offline tool to add context-specific heuristic_reasoning to resilience_evaluations.json

Usage:
    python tools/add_heuristic_reasoning.py --subscription-id <sub-id>
    
This script:
1. Reads resilience_evaluations.json for a subscription
2. Finds all checks with validation_source == "Heuristic" missing heuristic_reasoning
3. Generates specific, practical reasoning based on the recommendation and status
4. Writes the updated file back
"""

import json
import argparse
from pathlib import Path
from typing import Dict, Any

# Mapping of recommendation keywords to practical guidance
REASONING_TEMPLATES = {
    # VM and Scale Set recommendations
    "Azure Boost VMs": {
        "fail": "The {resource_name} is not configured with Azure Boost. Review the VM size and enable Azure Boost to improve performance for maintenance-sensitive workloads.",
        "pass": "The {resource_name} is configured with Azure Boost for improved performance."
    },
    "Scheduled Events": {
        "fail": "Scheduled Events is not enabled for {resource_name}. Enable 'scheduledEventsPolicy' in the VM configuration to receive notifications before maintenance events.",
        "pass": "The {resource_name} has Scheduled Events properly configured."
    },
    "Azure Linux VM Agent": {
        "fail": "The {resource_name} may not have the latest Azure Linux VM Agent. Update to the latest version to ensure optimal functionality and security patches.",
        "pass": "The {resource_name} is running a supported version of the Azure Linux VM Agent."
    },
    "Availability Zones": {
        "fail": "The {resource_name} is not deployed across multiple availability zones. Reconfigure the resource to span at least 2 zones for high availability.",
        "pass": "The {resource_name} is properly distributed across multiple availability zones."
    },
    "zone-redundant": {
        "fail": "The {resource_name} is not zone-redundant. Modify the configuration to enable zone-redundancy (typically by removing explicit zone settings or setting zones to null).",
        "pass": "The {resource_name} is configured as zone-redundant."
    },
    "Load Balancer": {
        "fail": "The {resource_name} Load Balancer configuration needs adjustment. Verify backend pool has 2+ instances and frontend is zone-redundant.",
        "pass": "The {resource_name} Load Balancer is properly configured for high availability."
    },
    "Storage": {
        "fail": "The {resource_name} storage account is not using the recommended configuration. Enable replication, versioning, or premium tier as needed.",
        "pass": "The {resource_name} storage account meets resilience recommendations."
    },
    "premium performance block blob": {
        "fail": "The {resource_name} is not configured for premium performance. Change the storage account type to 'Premium' and use BlockBlobStorage for high-performance requirements.",
        "pass": "The {resource_name} is using premium performance block blob storage."
    },
    "Monitor": {
        "fail": "Monitoring is not configured for {resource_name}. Enable Application Insights, Log Analytics, or diagnostic settings to track performance and errors.",
        "pass": "The {resource_name} has monitoring properly configured."
    },
    "Health check": {
        "fail": "Health check is not enabled for {resource_name}. Configure a health check endpoint in settings to enable automatic instance health monitoring.",
        "pass": "The {resource_name} has health check enabled."
    },
    "Autoscale": {
        "fail": "Autoscaling is not configured for {resource_name}. Enable autoscale rules based on CPU, memory, or request count to handle traffic variations.",
        "pass": "The {resource_name} has autoscaling properly configured."
    },
    "minimum instance": {
        "fail": "The {resource_name} has fewer than 2 instances configured. Increase the minimum instance count to 2 (or 3 if longer warmup time) for production workloads.",
        "pass": "The {resource_name} has the recommended minimum instance count configured."
    },
    "SSL": {
        "fail": "SSL/TLS is not enabled for {resource_name}. Configure HTTPS by obtaining and installing an SSL certificate.",
        "pass": "The {resource_name} has SSL/TLS properly configured."
    },
    "WAF": {
        "fail": "Web Application Firewall is not enabled for {resource_name}. Enable WAF policies to protect against common web exploits.",
        "pass": "The {resource_name} has Web Application Firewall enabled."
    },
    "encryption": {
        "fail": "Encryption is not enabled for {resource_name}. Enable encryption at rest and in transit using available encryption settings.",
        "pass": "The {resource_name} has encryption properly configured."
    },
    "backup": {
        "fail": "Backup/replication is not configured for {resource_name}. Enable backup or replication features to protect against data loss.",
        "pass": "The {resource_name} has backup/replication properly configured."
    },
    "Connection draining": {
        "fail": "Connection draining is not enabled for {resource_name}. Configure connection draining timeout in backend settings.",
        "pass": "The {resource_name} has connection draining properly configured."
    },
    "deployment": {
        "fail": "The {resource_name} deployment is not optimal. Review deployment configuration for redundancy and global distribution.",
        "pass": "The {resource_name} deployment is properly configured."
    },
    "NAT Gateway": {
        "fail": "NAT Gateway is not configured for {resource_name}. Replace outbound rules with NAT Gateway for better scalability.",
        "pass": "The {resource_name} is properly configured with NAT Gateway."
    },
    "Capacity Reservation": {
        "fail": "Capacity Reservation is not linked to {resource_name}. Create a capacity reservation group in the DR region and link it.",
        "pass": "The {resource_name} has Capacity Reservation properly configured."
    },
    "Separate storage for logs": {
        "fail": "The {resource_name} is using the same storage account for logs. Create a separate storage account to prevent log operations from impacting app performance.",
        "pass": "The {resource_name} uses a separate storage account for logs."
    },
    "staging slot": {
        "fail": "No staging slot is configured for {resource_name}. Create a deployment slot to test updates before production swap.",
        "pass": "The {resource_name} has a staging slot configured."
    },
    "Ensure Function App runs a supported version": {
        "fail": "The {resource_name} is running an unsupported Functions runtime version. Migrate to Functions runtime version 4.x.",
        "pass": "The {resource_name} is running a supported Functions runtime version."
    },
    "Regional location": {
        "fail": "The {resource_name} resource group may be in a different region than its resources. Ensure resources are in the same region as their resource groups.",
        "pass": "The {resource_name} and its resource group are in the same region."
    },
}

def get_practical_reasoning(description: str, status: str, resource_name: str) -> str:
    """Generate practical, specific reasoning based on recommendation description."""
    
    # Try to find a matching template
    for keyword, templates in REASONING_TEMPLATES.items():
        if keyword.lower() in description.lower():
            template = templates.get(status, templates.get("fail"))
            return template.format(resource_name=resource_name)
    
    # Fallback to generic but more practical message
    action = "should be remediated" if status == "fail" else "is properly configured"
    return f"The {resource_name} {action}. Review the '{description}' recommendation details and configuration settings."

def add_heuristic_reasoning(subscription_id: str, dry_run: bool = False, force: bool = False):
    """Add context-specific heuristic_reasoning to all Heuristic-validated checks."""
    
    # Get the resilience evaluations file
    data_dir = Path(__file__).parent.parent / "data" / subscription_id
    eval_file = data_dir / "resilience_evaluations.json"
    
    if not eval_file.exists():
        print(f"❌ File not found: {eval_file}")
        return False
    
    print(f"📖 Reading: {eval_file}")
    with open(eval_file) as f:
        data = json.load(f)
    
    evaluations = data.get("evaluations", {})
    updated_count = 0
    
    # Iterate through all resources and checks
    for resource_id, eval_data in evaluations.items():
        resource_name = eval_data.get("resource_name", "resource")
        checks = eval_data.get("checks", [])
        
        for check in checks:
            validation_source = check.get("validation_source", "")
            learn_more = check.get("learn_more", {})
            status = check.get("status", "pass")
            description = check.get("description", "Unknown")
            
            # Only process Heuristic validations that lack reasoning OR if force is set
            has_reasoning = learn_more.get("heuristic_reasoning")
            if validation_source == "Heuristic" and (not has_reasoning or force):
                # Generate practical reasoning
                reasoning = get_practical_reasoning(description, status, resource_name)
                
                # Ensure learn_more is a dict
                if not isinstance(learn_more, dict):
                    learn_more = {}
                
                # Add reasoning
                learn_more["heuristic_reasoning"] = reasoning
                check["learn_more"] = learn_more
                
                updated_count += 1
                
                desc_short = description[:50]
                marker = "↻" if has_reasoning else "✓"
                print(f"  {marker} {desc_short:<50} [{status}]")
    
    if updated_count == 0:
        print(f"✨ No updates needed")
        if not force:
            print("   (Use --force to replace existing reasoning)")
        return True
    
    print(f"\n📝 Updated: {updated_count} items with context-specific reasoning")
    
    if dry_run:
        print("🔍 DRY RUN - no changes written")
        return True
    
    # Write updated data back
    print(f"💾 Writing: {eval_file}")
    with open(eval_file, 'w') as f:
        json.dump(data, f, indent=2)
    
    print(f"✅ Complete! Added practical reasoning to {updated_count} items")
    return True

def main():
    parser = argparse.ArgumentParser(
        description="Add context-specific heuristic_reasoning to Heuristic-validated items"
    )
    parser.add_argument(
        "--subscription-id",
        required=True,
        help="Azure subscription ID"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without writing"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing reasoning with new practical versions"
    )
    
    args = parser.parse_args()
    
    success = add_heuristic_reasoning(args.subscription_id, dry_run=args.dry_run, force=args.force)
    exit(0 if success else 1)

if __name__ == "__main__":
    main()

