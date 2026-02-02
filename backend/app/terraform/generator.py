"""
Generator for converting Terraform resources to azure-workload-graph format.

Converts parsed Terraform resources and relationships into:
- resources.json: Standard Azure resource format compatible with collector
- edges.json: Relationship/dependency edges between resources
"""

import uuid
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime

from app.terraform.parser import TerraformResource, TerraformParser
from app.relationships.utils import short_id as compute_short_id


@dataclass
class GeneratedResource:
    """Resource in standard format."""
    id: str
    name: str
    type: str
    short_id: Optional[str] = None
    location: Optional[str] = None
    resource_group: Optional[str] = None
    subscription_id: Optional[str] = None
    tags: Dict[str, Any] = None
    properties: Dict[str, Any] = None
    sku: Optional[Dict[str, Any]] = None
    zones: Optional[List[str]] = None
    virtual: bool = True


class TerraformResourceGenerator:
    """
    Convert Terraform resources to standard azure-workload-graph format.
    
    Maps Terraform resource types and attributes to Azure REST API format.
    Generates relationships and edges between resources.
    """
    
    # Resource type mapping
    TYPE_MAP = {
        "azurerm_kubernetes_cluster": "Microsoft.ContainerService/managedClusters",
        "azurerm_resource_group": "Microsoft.Resources/resourceGroups",
        "azurerm_virtual_network": "Microsoft.Network/virtualNetworks",
        "azurerm_subnet": "Microsoft.Network/virtualNetworks/subnets",
        "azurerm_storage_account": "Microsoft.Storage/storageAccounts",
        "azurerm_storage_container": "Microsoft.Storage/storageAccounts/blobServices/containers",
        "azurerm_mssql_server": "Microsoft.Sql/servers",
        "azurerm_mssql_database": "Microsoft.Sql/servers/databases",
        "azurerm_cosmosdb_account": "Microsoft.DocumentDB/databaseAccounts",
        "azurerm_cosmosdb_sql_database": "Microsoft.DocumentDB/databaseAccounts/sqlDatabases",
        "azurerm_cosmosdb_sql_container": "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers",
        "azurerm_container_registry": "Microsoft.ContainerRegistry/registries",
        "azurerm_api_management": "Microsoft.ApiManagement/service",
        "azurerm_api_management_api": "Microsoft.ApiManagement/service/apis",
        "azurerm_api_management_api_operation": "Microsoft.ApiManagement/service/apis/operations",
        "azurerm_network_interface": "Microsoft.Network/networkInterfaces",
        "azurerm_virtual_machine": "Microsoft.Compute/virtualMachines",
        "azurerm_app_service": "Microsoft.Web/sites",
        "azurerm_function_app": "Microsoft.Web/sites",
        "azurerm_load_balancer": "Microsoft.Network/loadBalancers",
        "azurerm_application_gateway": "Microsoft.Network/applicationGateways",
        "azurerm_public_ip": "Microsoft.Network/publicIPAddresses",
        "azurerm_network_security_group": "Microsoft.Network/networkSecurityGroups",
        "azurerm_redis_cache": "Microsoft.Cache/redis",
        "azurerm_mysql_server": "Microsoft.DBforMySQL/servers",
        "azurerm_postgresql_server": "Microsoft.DBforPostgreSQL/servers",
        "azurerm_data_factory": "Microsoft.DataFactory/factories",
        "azurerm_synapse_workspace": "Microsoft.Synapse/workspaces",
        "azurerm_key_vault": "Microsoft.KeyVault/vaults",
    }
    
    def __init__(self, subscription_id: str, subscription_name: str = "Terraform"):
        """
        Initialize generator.
        
        Args:
            subscription_id: UUID for this Terraform deployment
            subscription_name: Friendly name for the subscription
        """
        self.subscription_id = subscription_id
        self.subscription_name = subscription_name
        self.terraform_resources: List[TerraformResource] = []
        self.generated_resources: Dict[str, GeneratedResource] = {}
        self.resource_id_map: Dict[str, str] = {}  # Maps terraform IDs to generated IDs
        self.edges: List[Dict[str, Any]] = []
    
    def add_resources(self, tf_resources: List[TerraformResource]):
        """
        Add Terraform resources to process.
        
        Args:
            tf_resources: List of parsed Terraform resources
        """
        self.terraform_resources.extend(tf_resources)
    
    def generate(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Generate resources.json and edges.json output.
        
        Returns:
            Tuple of (resources_output, edges_output)
            
        Example output:
            resources_output = {
                "subscription_id": "...",
                "subscription_name": "...",
                "resources": [...]
            }
            
            edges_output = {
                "subscription_id": "...",
                "subscription_name": "...",
                "edges": [...]
            }
        """
        # Convert Terraform resources to standard format
        self._convert_resources()

        # Enrich parent resources to align with Collector output format
        # - Embed subnet details inside VNet properties
        self._embed_vnet_subnets()
        
        # Generate relationships/edges
        self._generate_edges()
        
        # Build output dictionaries
        # Filter out synthetic Terraform resources and resource groups to match Collector output
        resources_list = [
            r for r in self.generated_resources.values()
            if not r.type.startswith("microsoft.unknown/")
            and r.type != "microsoft.resources/resourcegroups"
        ]
        
        # Get set of valid resource IDs for edge filtering
        valid_resource_ids = {r.id for r in resources_list}
        
        # Filter edges to only include those between valid resources
        filtered_edges = [
            edge for edge in self.edges
            if edge.get("source") in valid_resource_ids
            and edge.get("target") in valid_resource_ids
        ]
        
        resources_output = {
            "subscription_id": self.subscription_id,
            "subscription_name": self.subscription_name,
            "resources": [
                self._resource_to_dict(r) for r in resources_list
            ]
        }
        
        edges_output = {
            "subscription_id": self.subscription_id,
            "subscription_name": self.subscription_name,
            "edges": filtered_edges
        }
        
        return resources_output, edges_output

    def _embed_vnet_subnets(self) -> None:
        """Embed subnet information inside each VNet's properties.

        The Azure ARG collector includes a `properties.subnets` array within
        Virtual Network resources. Terraform-generated data models subnets as
        separate resources. To keep parity with the collector output and avoid
        downstream analyzer "Unknown" results, we enrich VNets with a subnets
        list assembled from child subnet resources.

        This keeps subnet resources as standalone entries and also mirrors
        the embedded structure expected by components that read VNet subnets
        from `properties.subnets`.
        """
        # Build quick lists of VNets and Subnets
        vnets: List[Tuple[str, GeneratedResource]] = []
        subnets: List[GeneratedResource] = []

        for res_id, res in self.generated_resources.items():
            res_type = (res.type or "").lower()
            if res_type == "microsoft.network/virtualnetworks":
                vnets.append((res_id, res))
            elif res_type == "microsoft.network/virtualnetworks/subnets":
                subnets.append(res)

        if not vnets or not subnets:
            return  # Nothing to embed

        # Index subnets by their parent VNet name (from properties.virtual_network_name)
        subnets_by_vnet: Dict[str, List[GeneratedResource]] = {}
        for sn in subnets:
            props = sn.properties or {}
            vnet_name = (props.get("virtual_network_name") or "").lower()
            if not vnet_name:
                # Try inferring from ID path if available
                rid = (sn.id or "").lower()
                # Expect .../virtualnetworks/<name>/subnets/<subnet>
                try:
                    parts = rid.split("/virtualnetworks/")
                    if len(parts) > 1:
                        vnet_part = parts[1]
                        vnet_name_in_id = vnet_part.split("/subnets/")[0]
                        vnet_name = vnet_name_in_id
                except Exception:
                    pass
            if vnet_name:
                subnets_by_vnet.setdefault(vnet_name, []).append(sn)

        # Attach matching subnets to each VNet's properties
        for vnet_id, vnet in vnets:
            vnet_name_key = (vnet.name or (vnet.properties or {}).get("name", "")).lower()
            if not vnet_name_key:
                continue

            vnet_props = vnet.properties or {}
            existing_subnets = vnet_props.get("subnets")
            # If already present (e.g., manually added), do not override
            if isinstance(existing_subnets, list) and existing_subnets:
                continue

            children = subnets_by_vnet.get(vnet_name_key, [])
            if not children:
                continue

            embedded_subnets: List[Dict[str, Any]] = []
            for sn in children:
                sn_props = sn.properties or {}
                # Map Terraform-style `address_prefixes` to Azure ARG `addressPrefix`
                address_prefix = None
                prefixes = sn_props.get("address_prefixes")
                if isinstance(prefixes, list) and prefixes:
                    address_prefix = prefixes[0]
                elif isinstance(prefixes, str):
                    address_prefix = prefixes

                embedded_subnets.append({
                    "properties": {
                        # Minimal fields to satisfy analyzer expectations
                        "addressPrefix": address_prefix,
                        # Policies/delegations omitted in Terraform virtual data
                        "delegations": [],
                        "privateLinkServiceNetworkPolicies": "Enabled",
                        "privateEndpointNetworkPolicies": "Enabled",
                    },
                    "name": sn.name,
                    "type": "Microsoft.Network/virtualNetworks/subnets",
                    "id": sn.id,
                })

            # Write back into VNet properties
            vnet_props["subnets"] = embedded_subnets
            vnet.properties = vnet_props
    
    def _resolve_resource_references(self, obj: Any) -> Any:
        """
        Recursively resolve Terraform resource references to Azure resource IDs.
        
        Replaces patterns like:
        - ${azurerm_subnet.aks.id} -> /subscriptions/.../subnets/...
        - azurerm_subnet.aks.id -> /subscriptions/.../subnets/...
        - var.kubernetes_version (if null) -> null or "[provider_default]"
        
        Args:
            obj: Any object (dict, list, str, or primitive)
            
        Returns:
            Object with resolved resource references
        """
        if isinstance(obj, dict):
            return {k: self._resolve_resource_references(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._resolve_resource_references(item) for item in obj]
        elif isinstance(obj, str):
            import re
            
            # Pattern: ${azurerm_type.name.id} or ${azurerm_type.name.attr} or ${azurerm_type.name.attr[0].field}
            def replace_interpolation(match):
                ref = match.group(1)  # e.g., azurerm_subnet.aks.id or azurerm_kubernetes_cluster.aks.kubelet_identity[0].object_id
                
                # Handle array accessors and nested paths - extract base resource reference
                # Example: azurerm_kubernetes_cluster.aks.kubelet_identity[0].object_id
                # We want: azurerm_kubernetes_cluster.aks
                base_match = re.match(r'(azurerm_[a-z_]+)\.([a-z0-9_-]+)', ref)
                if base_match:
                    resource_type = base_match.group(1)
                    resource_name = base_match.group(2)
                    
                    # Check if it's requesting the resource ID
                    if '.id' in ref and not '[' in ref.split('.id')[0]:
                        # Simple .id reference
                        tf_key = f"{resource_type}.{resource_name}"
                        azure_id = self.resource_id_map.get(tf_key)
                        if azure_id:
                            return azure_id
                    elif '[' in ref or ref.count('.') > 2:
                        # Complex reference like kubelet_identity[0].object_id
                        # For now, leave as-is since we can't resolve runtime attributes
                        # These would require Terraform state or apply-time values
                        return match.group(0)
                    else:
                        # Try simple resolution
                        parts = ref.split('.')
                        if len(parts) >= 3 and parts[2] == 'id':
                            tf_key = f"{resource_type}.{resource_name}"
                            azure_id = self.resource_id_map.get(tf_key)
                            if azure_id:
                                return azure_id
                return match.group(0)  # Return original if not resolved
            
            # Resolve ${azurerm_*.*.id} patterns
            result = re.sub(r'\$\{([^}]+)\}', replace_interpolation, obj)
            
            # Resolve bare azurerm_*.*.id patterns (without ${})
            result = re.sub(r'(azurerm_[a-z_]+\.[a-z_]+\.id)', 
                          lambda m: self.resource_id_map.get('.'.join(m.group(1).split('.')[:2]), m.group(0)) 
                                    if m.group(1).endswith('.id') else m.group(0), 
                          result)
            
            return result
        else:
            return obj
    
    def _convert_resources(self):
        """Convert each Terraform resource to standard format."""
        
        # First pass: Build resource ID map
        for tf_resource in self.terraform_resources:
            # Determine resource type
            azure_type = self.TYPE_MAP.get(
                tf_resource.type,
                f"Microsoft.Unknown/{tf_resource.type.replace('azurerm_', '')}"
            )
            
            # Generate resource ID
            resource_id = self._generate_resource_id(
                azure_type,
                tf_resource.name,
                self._get_resource_group(tf_resource.attributes),
            )
            self.resource_id_map[f"{tf_resource.type}.{tf_resource.name}"] = resource_id
        
        # Second pass: Create resources with resolved references
        for tf_resource in self.terraform_resources:
            # Determine resource type
            azure_type = self.TYPE_MAP.get(
                tf_resource.type,
                f"Microsoft.Unknown/{tf_resource.type.replace('azurerm_', '')}"
            )
            
            # Get resource ID from map
            resource_id = self.resource_id_map[f"{tf_resource.type}.{tf_resource.name}"]
            
            # Extract and resolve attributes
            attrs = self._resolve_resource_references(tf_resource.attributes)
            
            # Map to standard format
            resource = GeneratedResource(
                id=resource_id,
                short_id=compute_short_id(resource_id),
                name=self._get_resource_name(tf_resource, attrs),
                type=azure_type.lower(),
                location=self._get_location(tf_resource.type, attrs),
                resource_group=self._get_resource_group(attrs),
                subscription_id=self.subscription_id,
                tags=self._get_tags(attrs),
                properties=self._extract_properties(tf_resource.type, attrs),
                sku=self._get_sku(tf_resource.type, attrs),
                zones=self._get_zones(attrs),
                virtual=True,
            )
            
            self.generated_resources[resource_id] = resource
    
    def _generate_resource_id(self, resource_type: str, name: str, resource_group: Optional[str] = None) -> str:
        """
        Generate a resource ID matching Azure REST API format.
        
        Format: /subscriptions/{subscriptionId}/resourceGroups/{rg}/providers/{namespace}/{type}/{name}
        
        Args:
            resource_type: Azure resource type (e.g., Microsoft.Storage/storageAccounts)
            name: Resource name
            
        Returns:
            Resource ID string
        """
        # Extract namespace and resource type
        parts = resource_type.split("/")
        if len(parts) >= 2:
            # For types like "Microsoft.Storage/storageAccounts/blobServices/containers"
            # namespace = "Microsoft.Storage"
            # res_type = "storageAccounts/blobServices/containers"
            namespace = parts[0]
            res_type = "/".join(parts[1:])
        else:
            namespace = "Microsoft.Unknown"
            res_type = resource_type
        
        # Build ID
        rg = (resource_group or "terraform-rg").strip() or "terraform-rg"

        resource_id = (
            f"/subscriptions/{self.subscription_id}"
            f"/resourceGroups/{rg}"
            f"/providers/{namespace}/{res_type}/{name}"
        )
        
        # Normalize to lowercase for consistency with manual edges and Azure API
        return resource_id.lower()
    
    def _get_resource_name(
        self, tf_resource: TerraformResource, attrs: Dict[str, Any]
    ) -> str:
        """Extract resource name from Terraform."""
        # Priority: name attribute, Terraform name
        return attrs.get("name", tf_resource.name)
    
    def _get_location(self, tf_type: str, attrs: Dict[str, Any]) -> Optional[str]:
        """Extract location from attributes."""
        # Priority: location, region, azure_location
        return attrs.get("location") or attrs.get("region")
    
    def _get_resource_group(self, attrs: Dict[str, Any]) -> Optional[str]:
        """Extract resource group from attributes."""
        # Priority: resource_group_name
        return attrs.get("resource_group_name", "terraform-rg")
    
    def _get_tags(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """Extract tags from attributes."""
        return attrs.get("tags", {}) or {}
    
    def _get_sku(self, tf_type: str, attrs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract SKU and configuration information from resource attributes."""
        sku_info = {}
        
        # Priority 1: Direct sku_name attribute
        if "sku_name" in attrs:
            sku_info["name"] = attrs["sku_name"]
            return sku_info if sku_info else None
        
        if "sku" in attrs:
            if isinstance(attrs["sku"], dict):
                return attrs["sku"]
            else:
                return {"name": attrs["sku"]}
        
        # Priority 2: Type-specific configurations
        
        if tf_type == "azurerm_kubernetes_cluster":
            # Extract from default_node_pool
            if "default_node_pool" in attrs:
                pool = attrs["default_node_pool"]
                if isinstance(pool, dict):
                    vm_size = pool.get("vm_size")
                    if vm_size:
                        return {
                            "name": vm_size,
                            "tier": "Standard",  # AKS nodes are typically Standard
                            "capacity": pool.get("node_count", 1)
                        }
        
        elif tf_type == "azurerm_virtual_machine":
            if "vm_size" in attrs:
                return {"name": attrs["vm_size"]}
        
        elif tf_type == "azurerm_storage_account":
            # Extract storage tier configuration
            sku_info = {}
            if "account_tier" in attrs:
                sku_info["tier"] = attrs["account_tier"]
            if "account_replication_type" in attrs:
                sku_info["replication"] = attrs["account_replication_type"]
            if "access_tier" in attrs:
                sku_info["access_tier"] = attrs["access_tier"]
            return sku_info if sku_info else None
        
        elif tf_type == "azurerm_mssql_database":
            if "sku_name" in attrs:
                return {
                    "name": attrs["sku_name"],
                    "max_size_gb": attrs.get("max_size_gb"),
                    "auto_pause_delay": attrs.get("auto_pause_delay_in_minutes"),
                    "min_capacity": attrs.get("min_capacity"),
                    "zone_redundant": attrs.get("zone_redundant", False),
                }
        
        elif tf_type == "azurerm_cosmosdb_account":
            sku_info = {}
            if "offer_type" in attrs:
                sku_info["offer_type"] = attrs["offer_type"]
            if "kind" in attrs:
                sku_info["kind"] = attrs["kind"]
            if "capabilities" in attrs:
                caps = attrs["capabilities"]
                if isinstance(caps, list):
                    sku_info["capabilities"] = [c.get("name") if isinstance(c, dict) else c for c in caps]
                elif isinstance(caps, dict):
                    sku_info["capabilities"] = [caps.get("name", "")]
            return sku_info if sku_info else None
        
        elif tf_type == "azurerm_container_registry":
            if "sku" in attrs:
                return {"name": attrs["sku"]}
        
        elif tf_type == "azurerm_api_management":
            if "sku_name" in attrs:
                return {"name": attrs["sku_name"]}
        
        return None
    
    def _get_zones(self, attrs: Dict[str, Any]) -> Optional[List[str]]:
        """Extract availability zones."""
        zones = attrs.get("availability_zones")
        if isinstance(zones, list):
            return zones
        if isinstance(zones, str):
            # Parse array string
            try:
                import ast
                return ast.literal_eval(zones)
            except:
                return None
        return None
    
    def _extract_properties(
        self, tf_type: str, attrs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Extract relevant properties for this resource type.
        
        Maps Terraform attributes to Azure REST API properties.
        Includes detailed configuration information.
        """
        props = {}
        
        # Copy all attributes as properties (foundation)
        props.update(attrs)
        
        # Type-specific property extraction with detailed handling
        
        if tf_type == "azurerm_kubernetes_cluster":
            props["kubernetes_version"] = attrs.get("kubernetes_version")
            props["dns_prefix"] = attrs.get("dns_prefix")
            
            # Detailed node pool info
            if "default_node_pool" in attrs:
                pool = attrs["default_node_pool"]
                if isinstance(pool, dict):
                    props["nodePoolProfiles"] = [{
                        "name": pool.get("name", "agentpool"),
                        "count": pool.get("node_count", 1),
                        "vmSize": pool.get("vm_size", "Standard_DS2_v2"),
                        "osType": "Linux",
                        "availabilityZones": pool.get("availability_zones", []),
                        "maxPods": pool.get("max_pods", 30),
                        "osDiskSizeGB": pool.get("os_disk_size_gb", 128),
                        "type": pool.get("type", "VirtualMachineScaleSets"),
                        "enableAutoScaling": pool.get("enable_auto_scaling", False),
                    }]
            
            # Network profile
            if "network_profile" in attrs:
                net = attrs["network_profile"]
                if isinstance(net, dict):
                    props["networkProfile"] = {
                        "networkPlugin": net.get("network_plugin", "kubenet"),
                        "loadBalancerSku": net.get("load_balancer_sku", "standard"),
                        "outboundType": net.get("outbound_type", "loadBalancer"),
                    }
        
        elif tf_type == "azurerm_storage_account":
            props["account_tier"] = attrs.get("account_tier")
            props["account_replication_type"] = attrs.get("account_replication_type")
            props["access_tier"] = attrs.get("access_tier")
            props["min_tls_version"] = attrs.get("min_tls_version")
            props["allow_nested_items_to_be_public"] = attrs.get("allow_nested_items_to_be_public", False)
        
        elif tf_type == "azurerm_mssql_database":
            props["max_size_gb"] = attrs.get("max_size_gb")
            props["collation"] = attrs.get("collation")
            props["sku_name"] = attrs.get("sku_name")
            props["auto_pause_delay_in_minutes"] = attrs.get("auto_pause_delay_in_minutes")
            props["min_capacity"] = attrs.get("min_capacity")
            props["zone_redundant"] = attrs.get("zone_redundant")
            props["read_scale"] = attrs.get("read_scale")
        
        elif tf_type == "azurerm_mssql_server":
            props["version"] = attrs.get("version")
            props["administrator_login"] = attrs.get("administrator_login")
            props["minimum_tls_version"] = attrs.get("minimum_tls_version")
            props["public_network_access_enabled"] = attrs.get("public_network_access_enabled")
        
        elif tf_type == "azurerm_cosmosdb_account":
            props["offer_type"] = attrs.get("offer_type")
            props["kind"] = attrs.get("kind")
            
            # Consistency policy
            if "consistency_policy" in attrs:
                policy = attrs["consistency_policy"]
                if isinstance(policy, dict):
                    props["consistencyPolicy"] = {
                        "defaultConsistencyLevel": policy.get("consistency_level", "Session")
                    }
            
            # Geo locations
            if "geo_location" in attrs:
                geo = attrs["geo_location"]
                if isinstance(geo, list) and geo:
                    geo_list = geo if isinstance(geo, list) else [geo]
                    props["geoLocations"] = [{
                        "location": g.get("location") if isinstance(g, dict) else g,
                        "failoverPriority": g.get("failover_priority", 0) if isinstance(g, dict) else 0,
                    } for g in geo_list]
            
            props["enable_free_tier"] = attrs.get("enable_free_tier", False)
            props["public_network_access_enabled"] = attrs.get("public_network_access_enabled")
        
        elif tf_type == "azurerm_container_registry":
            props["sku"] = attrs.get("sku")
            props["admin_enabled"] = attrs.get("admin_enabled", False)
        
        elif tf_type == "azurerm_api_management":
            props["sku_name"] = attrs.get("sku_name")
            props["publisher_name"] = attrs.get("publisher_name")
            props["publisher_email"] = attrs.get("publisher_email")
        
        elif tf_type == "azurerm_virtual_network":
            props["address_space"] = attrs.get("address_space", [])
        
        elif tf_type == "azurerm_subnet":
            props["address_prefixes"] = attrs.get("address_prefixes", [])
        
        elif tf_type == "azurerm_kubernetes_cluster" or tf_type == "azurerm_virtual_machine":
            # Add availability zones if present
            if "availability_zones" in attrs:
                props["availabilityZones"] = attrs["availability_zones"]
        
        return props
    
    def _generate_edges(self):
        """
        Generate relationship edges between resources based on references.
        
        Detects:
        - Parent-child relationships (database -> server, subnet -> vnet, etc.)
        - Resource group associations
        - Network relationships
        - Service dependencies
        """
        
        for res_id, resource in self.generated_resources.items():
            props = resource.properties or {}
            
            # Define reference attributes and their relationship types
            reference_patterns = {
                # Resource group associations
                'resource_group_name': ('contained_in', 'resource_group'),
                'resource_group': ('contained_in', 'resource_group'),
                
                # Network relationships
                'vnet_subnet_id': ('attached_to', 'subnet'),
                'subnet_id': ('attached_to', 'subnet'),
                # VM/NIC relationships
                'network_interface_id': ('attached_to', 'nic'),
                'network_interface_ids': ('attached_to', 'nic'),
                
                # Storage associations
                'storage_account_name': ('uses', 'storage_account'),
                'storage_account_id': ('uses', 'storage_account'),
                
                # ACR associations
                'container_registry_id': ('uses', 'acr'),
                'container_registry_name': ('uses', 'acr'),
                
                # Key Vault associations
                'key_vault_id': ('uses', 'key_vault'),
                'key_vault_name': ('uses', 'key_vault'),

                # Role assignment scope relations (e.g., grants/applies_to target resource)
                'scope': ('applies_to', 'any'),
            }
            
            # Recursively check all properties (including nested dicts/lists) for references
            for prop_name, prop_value in self._iter_reference_properties(props):
                if not isinstance(prop_value, str):
                    continue
                
                # Check if this property matches a reference pattern
                for attr_pattern, (relationship, target_type) in reference_patterns.items():
                    if attr_pattern.lower() in (prop_name or "").lower():
                        # Found a reference property
                        target_id = self._find_resource_by_reference(prop_value, target_type)
                        if target_id:
                            # Add edge from this resource to the referenced target
                            # Example: AKS attached_to Subnet, Resource contained_in Resource Group
                            self._add_edge(res_id, target_id, relationship, "Terraform")
                        break
            
            # Handle specific parent-child relationships based on resource type
            if resource.type.lower() == "microsoft.sql/servers/databases":
                # Database -> Server (parent)
                server_id = self._find_server_reference(props, resource.name)
                if server_id:
                    self._add_edge(server_id, res_id, "contains", "Terraform")
            
            elif resource.type.lower() == "microsoft.documentdb/databaseaccounts/sqldatabases":
                # Cosmos DB database -> Account (parent)
                account_id = self._find_cosmos_account_reference(props)
                if account_id:
                    self._add_edge(account_id, res_id, "contains", "Terraform")
                
                # Cosmos Database -> Containers (this database contains containers)
                db_name = resource.name or props.get("name")
                if db_name:
                    for cont_id, cont in self.generated_resources.items():
                        if cont.type.lower() == "microsoft.documentdb/databaseaccounts/sqldatabases/containers":
                            cont_props = cont.properties or {}
                            if (cont_props.get("database_name") or "").lower() == db_name.lower():
                                self._add_edge(res_id, cont_id, "contains", "Terraform")
            
            elif resource.type.lower() == "microsoft.network/virtualnetworks/subnets":
                # Subnet -> VNet (subnet depends on VNet, cannot exist without it)
                vnet_id = self._find_vnet_reference(props)
                if vnet_id:
                    # Subnet is source (dependent), VNet is target (can exist independently)
                    self._add_edge(res_id, vnet_id, "be_contained_in", "Terraform")
            
            elif resource.type.lower() == "microsoft.network/networkinterfaces":
                # NIC -> Subnet relationship
                subnet_id = self._find_subnet_reference(props)
                if subnet_id:
                    # NIC attaches to Subnet (consistent with attached_to semantics)
                    self._add_edge(res_id, subnet_id, "attached_to", "Terraform")

            elif resource.type.lower() == "microsoft.apimanagement/service":
                # APIM Service -> APIs (parent contains child)
                service_name = resource.name or props.get("name")
                if service_name:
                    for child_id, child in self.generated_resources.items():
                        if child.type.lower() == "microsoft.apimanagement/service/apis":
                            child_props = child.properties or {}
                            if (child_props.get("api_management_name") or "").lower() == service_name.lower():
                                self._add_edge(res_id, child_id, "contains", "Terraform")

            elif resource.type.lower() == "microsoft.apimanagement/service/apis":
                # API -> Operations (parent contains child)
                api_name = resource.name or props.get("name")
                if api_name:
                    for op_id, op in self.generated_resources.items():
                        if op.type.lower() == "microsoft.apimanagement/service/apis/operations":
                            op_props = op.properties or {}
                            if (op_props.get("api_name") or "").lower() == api_name.lower():
                                self._add_edge(res_id, op_id, "contains", "Terraform")

            elif resource.type.lower() == "microsoft.documentdb/databaseaccounts":
                # Cosmos Account -> Databases (parent contains child)
                account_name = resource.name or props.get("name")
                if account_name:
                    for db_id, db in self.generated_resources.items():
                        if db.type.lower() == "microsoft.documentdb/databaseaccounts/sqldatabases":
                            db_props = db.properties or {}
                            if (db_props.get("account_name") or "").lower() == account_name.lower():
                                self._add_edge(res_id, db_id, "contains", "Terraform")

    def _iter_reference_properties(self, obj: Any, parent_key: Optional[str] = None):
        """
        Yield (key, value) pairs for all string properties within nested dicts/lists.
        Only yields leaf keys paired with string values. Lists are traversed.
        """
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (dict, list)):
                    yield from self._iter_reference_properties(v, k)
                else:
                    yield (k, v)
        elif isinstance(obj, list):
            for item in obj:
                # For list items, propagate the parent key to nested structures
                if isinstance(item, (dict, list)):
                    yield from self._iter_reference_properties(item, parent_key)
                else:
                    # List items that are strings can be yielded with the parent key
                    yield (parent_key, item)
    
    def _find_resource_by_reference(self, ref_value: str, target_type: str) -> Optional[str]:
        """
        Find a resource ID based on a reference value.
        
        Args:
            ref_value: The reference value (e.g., resource name, ID, or template string)
            target_type: The type of resource being looked for (e.g., 'resource_group', 'sql_server')
        
        Returns:
            Resource ID if found, None otherwise
        """
        if not ref_value:
            return None
        
        # Resolve template references first
        resolved_ref = self._resolve_id_reference(ref_value)
        
        # If it resolved to an actual ID (starts with /subscriptions/), use it
        if resolved_ref.startswith('/subscriptions/'):
            return resolved_ref
        
        # Otherwise, try to find by name matching
        ref_lower = resolved_ref.lower() if isinstance(resolved_ref, str) else ""
        
        for res_id, resource in self.generated_resources.items():
            resource_name = resource.name.lower() if resource.name else ""
            
            # Type-specific matching
            if target_type == 'resource_group':
                if 'resourcegroups' in resource.id.lower() and ref_lower in resource.name.lower():
                    return resource.id
            
            elif target_type == 'sql_server':
                if 'sql/servers' in resource.id.lower() and 'database' not in resource.id.lower():
                    return resource.id
            
            elif target_type == 'cosmos_account':
                if 'cosmos' in resource.id.lower() or 'documentdb' in resource.id.lower():
                    if 'database' not in resource.id.lower():
                        return resource.id
            
            elif target_type == 'subnet':
                if 'subnet' in resource.id.lower() and ref_lower in resource.name.lower():
                    return resource.id
            
            elif target_type == 'vnet':
                if 'virtualnetwork' in resource.id.lower() and 'subnet' not in resource.id.lower():
                    if ref_lower in resource.name.lower():
                        return resource.id

            elif target_type == 'nic':
                # Network Interface
                if 'networkinterfaces' in resource.id.lower():
                    # Prefer name match when available, else accept any NIC
                    if not ref_lower or ref_lower in resource.name.lower():
                        return resource.id
            
            elif target_type == 'storage_account':
                if 'storage' in resource.id.lower() and ref_lower in resource.name.lower():
                    return resource.id
            
            elif target_type == 'acr':
                if 'containerregistry' in resource.id.lower() and ref_lower in resource.name.lower():
                    return resource.id
            
            elif target_type == 'key_vault':
                if 'keyvault' in resource.id.lower() and ref_lower in resource.name.lower():
                    return resource.id

            elif target_type == 'apim':
                # API Management service
                if 'apimanagement/service' in resource.id.lower() and ref_lower in resource.name.lower():
                    return resource.id

            elif target_type == 'apim_api':
                # API under API Management
                if 'apimanagement/service/apis' in resource.id.lower() and ref_lower in resource.name.lower():
                    return resource.id
        
        return None
    
    
    def _find_server_reference(self, props: Dict[str, Any], db_name: str) -> Optional[str]:
        """Find SQL server reference for database."""
        server_id = props.get("server_id")
        if server_id:
            return server_id
        
        # Try to find in resource list by name pattern
        for res_id, resource in self.generated_resources.items():
            if "sql" in resource.type.lower() and "database" not in resource.type.lower():
                return res_id
        
        return None
    
    def _resolve_id_reference(self, ref: str) -> str:
        """
        Resolve a reference string to an actual resource ID.
        
        Examples:
        - "${azurerm_mssql_server.sql.id}" -> "/subscriptions/.../Microsoft.Sql/servers/..."
        - "some-value" -> "some-value" (unchanged)
        """
        if not ref or not isinstance(ref, str):
            return ref
        
        # Check if it's a template reference like ${azurerm_...}
        if ref.startswith('${') and ref.endswith('}'):
            # Extract the reference (e.g., "azurerm_mssql_server.sql.id")
            inner = ref[2:-1]  # Remove ${ and }
            
            # Try to find matching resource in generated_resources
            for res_id, resource in self.generated_resources.items():
                # Check if this resource matches the reference
                # Pattern: azurerm_TYPE.NAME.ATTR -> look for Microsoft.*/NAME
                if inner.startswith('azurerm_'):
                    # Extract the name part (e.g., "sql" from "azurerm_mssql_server.sql.id")
                    parts = inner.split('.')
                    if len(parts) >= 2:
                        resource_name = parts[1]
                        # Check if resource name matches
                        if resource_name in resource.id:
                            return resource.id
            
            # If not found, return the original reference
            return ref
        
        # Not a template reference, return as-is
        return ref
    
    
    def _find_cosmos_account_reference(self, props: Dict[str, Any]) -> Optional[str]:
        """Find Cosmos DB account reference."""
        account_name = props.get("account_name")
        
        if account_name:
            for res_id, resource in self.generated_resources.items():
                if account_name in resource.id:
                    return res_id
        
        return None
    
    def _find_vnet_reference(self, props: Dict[str, Any]) -> Optional[str]:
        """Find VNet reference for subnet."""
        vnet_name = props.get("virtual_network_name")
        vnet_id = props.get("vnet_id")
        
        if vnet_id:
            return vnet_id
        
        if vnet_name:
            # Match by name (case-insensitive)
            vnet_name_lower = vnet_name.lower()
            for res_id, resource in self.generated_resources.items():
                if resource.type.lower() == "microsoft.network/virtualnetworks":
                    res_name = (resource.name or resource.properties.get("name", "")).lower()
                    if res_name == vnet_name_lower:
                        return res_id
        
        return None
    
    def _find_subnet_reference(self, props: Dict[str, Any]) -> Optional[str]:
        """Find subnet reference for NIC."""
        subnet_id = props.get("subnet_id")
        
        if subnet_id:
            return subnet_id
        
        return None
    
    def _add_edge(
        self, source_id: str, target_id: str, relationship: str, origin: str
    ):
        """
        Add an edge between two resources.
        
        Args:
            source_id: Source resource ID
            target_id: Target resource ID
            relationship: Type of relationship (e.g., "contains", "attaches", "uses")
            origin: Origin of this edge (e.g., "Terraform")
        """
        # Resolve any template strings in IDs (e.g., ${azurerm_...id} -> actual ID)
        source_id = self._resolve_id_reference(source_id)
        target_id = self._resolve_id_reference(target_id)
        
        edge_id = f"{source_id}|{relationship}|{target_id}".replace("/", "_")
        
        edge = {
            "id": edge_id,
            "source": source_id,
            "target": target_id,
            "relationship": relationship,
            "signals": ["Terraform"],
            "signal_details": [
                {
                    "type": "Terraform",
                    "source": "Terraform Configuration",
                    "timestamp": datetime.utcnow().isoformat(),
                    "confidence": 1.0,
                    "details": f"Implicit relationship from Terraform {relationship}"
                }
            ],
            "confidence": 1.0,
            "evidence": [
                {
                    "type": "Terraform",
                    "description": f"Terraform defines {relationship} relationship",
                    "source": "Terraform Configuration"
                }
            ],
            "origin": origin,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        self.edges.append(edge)
    
    def _resource_to_dict(self, resource: GeneratedResource) -> Dict[str, Any]:
        """Convert GeneratedResource to dictionary."""
        return {
            "id": resource.id,
            "short_id": resource.short_id,
            "name": resource.name,
            "type": resource.type,
            "location": resource.location,
            "resource_group": resource.resource_group,
            "subscription_id": resource.subscription_id,
            "tags": resource.tags or {},
            "properties": resource.properties or {},
            "sku": resource.sku,
            "zones": resource.zones,
            "virtual": bool(resource.virtual),
        }


def generate_from_terraform(
    terraform_files: List[str],
    subscription_id: Optional[str] = None,
    subscription_name: str = "Terraform",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Convenience function to generate resources and edges from Terraform files.
    
    Args:
        terraform_files: List of Terraform file paths
        subscription_id: UUID for subscription (auto-generated if None)
        subscription_name: Friendly name for subscription
        
    Returns:
        Tuple of (resources_output, edges_output) dictionaries
    """
    from pathlib import Path
    
    if not subscription_id:
        subscription_id = str(uuid.uuid4())
    
    parser = TerraformParser()
    generator = TerraformResourceGenerator(subscription_id, subscription_name)
    
    # Parse all files
    all_resources = []
    for tf_file in terraform_files:
        resources = parser.parse_file(Path(tf_file))
        all_resources.extend(resources)
    
    # Generate
    generator.add_resources(all_resources)
    return generator.generate()
