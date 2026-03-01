"""
LLM-based icon mapping generator.

This script uses an LLM to create a mapping between Azure resource types
and available icon files. It should be run once to generate the mapping.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Set

from app.settings import load_settings
from app.llm.model_client import FoundryModelClient, create_model_client


def get_all_icon_files(icons_dir: Path) -> Dict[str, List[str]]:
    """Scan all icon files organized by category."""
    icon_map = {}
    
    for category_dir in icons_dir.iterdir():
        if category_dir.is_dir() and not category_dir.name.startswith('.'):
            category_name = category_dir.name
            icons = []
            
            for icon_file in category_dir.iterdir():
                if icon_file.is_file() and icon_file.suffix == '.svg':
                    icons.append(icon_file.name)
            
            if icons:
                icon_map[category_name] = sorted(icons)
    
    return icon_map


def get_microsoft_resource_types() -> Set[str]:
    """
    Get comprehensive list of Azure resource types from Microsoft's ARI documentation.
    Source: https://github.com/microsoft/ARI/blob/main/docs/advanced/resource-types.md
    """
    resource_types = {
        # AI
        "microsoft.cognitiveservices/accounts",
        "microsoft.machinelearningservices/workspaces",
        "microsoft.search/searchservices",
        "microsoft.botservice/botservices",
        
        # Analytics
        "microsoft.databricks/workspaces",
        "microsoft.kusto/clusters",
        "microsoft.eventhub/namespaces",
        "microsoft.purview/accounts",
        "microsoft.streamanalytics/clusters",
        "microsoft.streamanalytics/streamingjobs",
        "microsoft.synapse/workspaces",
        "microsoft.eventgrid/systemtopics",
        
        # Compute
        "microsoft.compute/availabilitysets",
        "microsoft.desktopvirtualization/hostpools",
        "microsoft.desktopvirtualization/hostpools/sessionhosts",
        "microsoft.classiccompute/domainnames",
        "microsoft.compute/virtualmachines",
        "microsoft.compute/virtualmachines/extensions",
        "microsoft.compute/virtualmachinescalesets",
        "microsoft.compute/disks",
        "microsoft.avs/privateclouds",
        "microsoft.compute/sshpublickeys",
        
        # Container
        "microsoft.containerservice/managedclusters",
        "microsoft.redhatopenshift/openshiftclusters",
        "microsoft.app/containerapps",
        "microsoft.app/managedenvironments",
        "microsoft.containerinstance/containergroups",
        "microsoft.containerregistry/registries",
        
        # Database
        "microsoft.documentdb/databaseaccounts",
        "microsoft.dbformariadb/servers",
        "microsoft.dbformysql/servers",
        "microsoft.dbformysql/flexibleservers",
        "microsoft.dbforpostgresql/servers",
        "microsoft.dbforpostgresql/flexibleservers",
        "microsoft.cache/redis",
        "microsoft.cache/redisenterprise",
        "microsoft.sql/servers/databases",
        "microsoft.sql/managedinstances",
        "microsoft.sql/managedinstances/databases",
        "microsoft.sql/servers/elasticpools",
        "microsoft.sql/servers",
        "microsoft.sqlvirtualmachine/sqlvirtualmachines",
        
        # Hybrid
        "microsoft.hybridcompute/machines",
        
        # Integration
        "microsoft.apimanagement/service",
        "microsoft.servicebus/namespaces",
        "microsoft.logic/workflows",
        
        # IoT
        "microsoft.devices/iothubs",
        
        # Management
        "microsoft.automation/automationaccounts",
        "microsoft.automation/automationaccounts/runbooks",
        "microsoft.recoveryservices/vaults/backuppolicies",
        "microsoft.recoveryservices/vaults/backupfabrics/protectioncontainers/protecteditems",
        "microsoft.recoveryservices/vaults",
        "microsoft.alertsmanagement/smartdetectoralertrules",
        
        # Monitoring
        "microsoft.insights/components",
        "microsoft.operationalinsights/workspaces",
        "microsoft.insights/actiongroups",
        
        # Network
        "microsoft.network/bastionhosts",
        "microsoft.network/connections",
        "microsoft.network/expressroutecircuits",
        "microsoft.network/loadbalancers",
        "microsoft.network/natgateways",
        "microsoft.network/dnszones",
        "microsoft.network/routetables",
        "microsoft.network/trafficmanagerprofiles",
        "microsoft.network/virtualnetworks",
        "microsoft.network/applicationgateways",
        "microsoft.network/applicationgatewaywebapplicationfirewallpolicies",
        "microsoft.network/azurefirewalls",
        "microsoft.network/firewallpolicies",
        "microsoft.network/firewallpolicies/rulecollectiongroups",
        "microsoft.network/frontdoors",
        "microsoft.network/networkinterfaces",
        "microsoft.network/networksecuritygroups",
        "microsoft.network/networkwatchers/flowlogs",
        "microsoft.network/privatednszones",
        "microsoft.network/privatednszones/virtualnetworklinks",
        "microsoft.network/privateendpoints",
        "microsoft.network/publicipaddresses",
        "microsoft.network/virtualnetworkgateways",
        "microsoft.network/virtualwans",
        "microsoft.network/virtualhubs",
        "microsoft.network/vpnsites",
        "microsoft.network/networkwatchers",
        
        # Security
        "microsoft.keyvault/vaults",
        
        # Storage
        "microsoft.storage/storageaccounts",
        "microsoft.storagecache/caches",
        "microsoft.datacatalog/catalogs",
        
        # Web
        "microsoft.web/sites",
        "microsoft.web/sites/slots",
        "microsoft.web/serverfarms",
        "microsoft.web/hostingenvironments",
        "microsoft.web/hostingenvironments/workerpools",
        
        # Additional common types
        "microsoft.operationsmanagement/solutions",
        "microsoft.operationalinsights/querypacks",
        "microsoft.network/networksecurityperimeters",
        "microsoft.appconfiguration/configurationstores",
        "microsoft.azureactivedirectory/b2cdirectories",
        "microsoft.devtestlab/schedules",
    }
    
    return resource_types


def generate_icon_mappings_with_llm(
    resource_types: List[str],
    icon_categories: Dict[str, List[str]],
    model_client: FoundryModelClient,
    model: Optional[str],
    batch_size: int = 100
) -> Dict[str, str]:
    """Use LLM to generate mappings from resource types to icon paths in batches."""
    
    all_mappings = {}
    
    # Process in batches to avoid token limits
    for i in range(0, len(resource_types), batch_size):
        batch = resource_types[i:i+batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(resource_types) + batch_size - 1) // batch_size
        
        print(f"\nProcessing batch {batch_num}/{total_batches} ({len(batch)} resource types)...")
        
        # Prepare the prompt for this batch
        prompt = f"""Map each Azure resource type to the best icon path.

Resource types ({len(batch)}):
{json.dumps(batch, indent=2)}

Available icon files by category:
{json.dumps(icon_categories, indent=2)}

Rules:
1. Output key = resource type, value = category/icon-filename.svg
2. Choose the closest semantic icon based on provider/type name
3. If no good match exists, use general/10001-icon-service-All-Resources.svg
4. Return mappings for all resource types in this batch

Return valid JSON only (no markdown):
{{
    "microsoft.app/containerapps": "containers/02989-icon-service-Container-Apps-Environments.svg"
}}"""

        try:
            batch_mappings = model_client.generate_json(
                system_prompt=(
                    "You are an expert system that generates accurate Azure resource type to icon mappings. "
                    "Output only valid JSON without markdown formatting. "
                    "Map every single resource type provided."
                ),
                user_prompt=prompt,
                temperature=0.3,
                max_tokens=16000,
                model=model,
            )

            if not isinstance(batch_mappings, dict):
                raise ValueError("Invalid mapping payload returned by LLM")

            all_mappings.update(batch_mappings)
            print(f"  ✓ Generated {len(batch_mappings)} mappings for this batch")
            
        except Exception as e:
            print(f"  ✗ Error processing batch {batch_num}: {e}")
    
    return all_mappings


def main():
    """Generate icon mappings and save to file."""
    
    # Get paths
    settings = load_settings()
    backend_dir = Path(__file__).parent.parent.parent
    frontend_dir = backend_dir.parent / "frontend"
    icons_dir = frontend_dir / "public" / "Icons"
    data_dir = Path(settings.get_data_dir())
    output_file = data_dir / "iconMappings.json"
    
    print(f"Scanning icons from: {icons_dir}")
    
    # Collect data
    icon_categories = get_all_icon_files(icons_dir)
    resource_types = get_microsoft_resource_types()
    
    print(f"\nFound {len(icon_categories)} icon categories")
    print(f"Using {len(resource_types)} Azure resource types from Microsoft ARI documentation")
    
    # Initialize direct Foundry model client
    model_client = create_model_client(settings)
    if not model_client.is_available():
        print("Foundry model client unavailable; aborting icon generation.")
        return

    ai_agent_cfg = settings.get_ai_agent_config()
    deployment = ai_agent_cfg.get("reasoning_model") or settings.get_llm_generation_config().get("model")
    if not deployment:
        print("Reasoning model not configured; set ai_agent.reasoning_model.")
        return

    # Generate mappings with LLM
    mappings = generate_icon_mappings_with_llm(
        sorted(resource_types),
        icon_categories,
        model_client,
        deployment,
    )
    
    if not mappings:
        print("Failed to generate mappings!")
        return
    
    print(f"\nGenerated {len(mappings)} icon mappings")
    
    # Save to file
    os.makedirs(output_file.parent, exist_ok=True)
    with open(output_file, 'w') as f:
        json.dump(mappings, f, indent=2)
    
    print(f"\nSaved icon mappings to: {output_file}")
    
    # Show some examples
    print("\nSample mappings:")
    for resource_type in sorted(mappings.keys())[:10]:
        print(f"  {resource_type} -> {mappings[resource_type]}")


if __name__ == "__main__":
    main()
