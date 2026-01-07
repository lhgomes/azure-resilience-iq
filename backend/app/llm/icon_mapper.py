"""
LLM-based icon mapping generator.

This script uses an LLM to create a mapping between Azure resource types
and available icon files. It should be run once to generate the mapping.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Set
from azure.identity import DefaultAzureCredential
from openai import AzureOpenAI
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


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
    client: AzureOpenAI,
    batch_size: int = 100
) -> Dict[str, str]:
    """Use LLM to generate mappings from resource types to icon paths in batches."""
    
    all_mappings = {}
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    
    # Process in batches to avoid token limits
    for i in range(0, len(resource_types), batch_size):
        batch = resource_types[i:i+batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(resource_types) + batch_size - 1) // batch_size
        
        print(f"\nProcessing batch {batch_num}/{total_batches} ({len(batch)} resource types)...")
        
        # Prepare the prompt for this batch
        prompt = f"""You are an expert in Azure resource types and icon mapping.

I have a batch of {len(batch)} Azure resource types and {sum(len(icons) for icons in icon_categories.values())} icon files.

Azure Resource Types for this batch:
{json.dumps(batch, indent=2)}

Available Icon Categories and Files (showing sample):
{json.dumps({k: v[:5] + (['...'] if len(v) > 5 else []) for k, v in list(icon_categories.items())[:15]}, indent=2)}

Full icon list available in all categories:
{json.dumps(icon_categories, indent=2)}

Your task: Create a JSON mapping from each Azure resource type to the BEST matching icon file path.

Rules:
1. Map resource type (e.g., "microsoft.app/containerapps") to icon path (e.g., "containers/02989-icon-service-Container-Apps-Environments.svg")
2. Choose the most semantically appropriate icon for each resource type
3. Parse the resource type: "microsoft.CATEGORY/RESOURCETYPE" - use CATEGORY and RESOURCETYPE to find matches
4. Icon filenames often match service names (e.g., "Container-Apps", "Virtual-Machines", "Kubernetes")
5. If no good match exists, use "general/10001-icon-service-All-Resources.svg"
6. Output format: category folder + "/" + icon filename

Output ONLY valid JSON (no markdown, no explanation):
{{
  "microsoft.app/containerapps": "containers/02989-icon-service-Container-Apps-Environments.svg",
  ...
}}

Map ALL {len(batch)} resource types in this batch:
"""

        try:
            response = client.chat.completions.create(
                model=deployment,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert system that generates accurate Azure resource type to icon mappings. Output only valid JSON without markdown formatting. Map every single resource type provided."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.3,
                max_tokens=16000
            )
            
            response_text = response.choices[0].message.content.strip()
            
            # Remove markdown code blocks if present
            if response_text.startswith('```'):
                lines = response_text.split('\n')
                response_text = '\n'.join(lines[1:-1]) if len(lines) > 2 else response_text
                if response_text.startswith('json'):
                    response_text = response_text[4:].strip()
            
            batch_mappings = json.loads(response_text)
            all_mappings.update(batch_mappings)
            print(f"  ✓ Generated {len(batch_mappings)} mappings for this batch")
            
        except json.JSONDecodeError as e:
            print(f"  ✗ Failed to parse LLM response for batch {batch_num}: {e}")
            print(f"  Response preview: {response_text[:200]}")
        except Exception as e:
            print(f"  ✗ Error processing batch {batch_num}: {e}")
    
    return all_mappings


def main():
    """Generate icon mappings and save to file."""
    
    # Get paths
    backend_dir = Path(__file__).parent.parent.parent
    frontend_dir = backend_dir.parent / "frontend"
    icons_dir = frontend_dir / "public" / "Icons"
    data_dir = backend_dir / "data"
    output_file = data_dir / "iconMappings.json"
    
    print(f"Scanning icons from: {icons_dir}")
    
    # Collect data
    icon_categories = get_all_icon_files(icons_dir)
    resource_types = get_microsoft_resource_types()
    
    print(f"\nFound {len(icon_categories)} icon categories")
    print(f"Using {len(resource_types)} Azure resource types from Microsoft ARI documentation")
    
    # Initialize Azure OpenAI client
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-05-01-preview")
    api_key = os.getenv("AZURE_OPENAI_KEY")
    
    if api_key:
        client = AzureOpenAI(
            api_key=api_key,
            api_version=api_version,
            azure_endpoint=endpoint,
        )
    else:
        # Use managed identity
        credential = DefaultAzureCredential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")
        client = AzureOpenAI(
            api_key=token.token,
            api_version=api_version,
            azure_endpoint=endpoint,
        )
    
    # Generate mappings with LLM
    mappings = generate_icon_mappings_with_llm(
        sorted(resource_types),
        icon_categories,
        client
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
