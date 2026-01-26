"""
Zonal Resiliency Analysis Module

Analyzes Azure resources for Availability Zone configuration and compliance
with Azure's 3-AZ (Availability Zone) best practices.

This module provides:
- Zone detection across different Azure resource types
- Zone redundancy identification
- 3-AZ compliance assessment
- Summary statistics and recommendations

Configuration:
- Zone support configuration is loaded from config/zone_support.yaml
- To update the list of zone-aware services, edit that file

Usage:
    from app.resilience.zonal_analyzer import ZonalAnalyzer, ZonalResiliencySummary
    
    zonal_data = ZonalAnalyzer.extract_zonal_data(resource)
    summary = ZonalResiliencySummary(all_zonal_data)
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Set
from pathlib import Path
import re
import yaml
import logging

from app.resilience.zone_recommendation_engine import ZoneRecommendationEngine, DeploymentPattern

logger = logging.getLogger(__name__)


# Remove DeploymentPattern class definition from here since it's now in zone_recommendation_engine.py


@dataclass
class ZonalData:
    """
    Zone configuration data for a single Azure resource.
    
    Attributes:
        zones_used: List of zone identifiers (e.g., ["1", "2", "3"])
        is_zone_redundant: True if resource is automatically zone-redundant
        zone_count: Number of zones used (0 if unknown/zone-redundant)
        meets_3az_requirement: True if deployed across all 3 availability zones
        deployment_pattern: The zone deployment pattern
        recommendation: Human-readable recommendation text
    """
    zones_used: List[str]
    is_zone_redundant: bool
    zone_count: int
    meets_3az_requirement: bool
    deployment_pattern: DeploymentPattern
    recommendation: str


class ZonalAnalyzer:
    """
    Analyzer for Azure resource zone configuration.
    
    Detects and classifies zone deployment patterns across various Azure
    resource types following Azure Well-Architected Framework best practices.
    
    Configuration is loaded from config/zone_support.yaml which defines:
    - no_zone_support: Resources that don't support availability zones
    - zone_redundant_by_default: Resources that are automatically zone-redundant
    - zone_support_available: Resources that can be configured for zones
    """
    
    # Class-level cache for configuration
    _config_cache: Optional[Dict[str, Any]] = None
    _config_path = Path(__file__).parent.parent.parent / "config" / "zone_support.yaml"
    
    @classmethod
    def _load_config(cls) -> Dict[str, Any]:
        """
        Load zone support configuration from YAML file.
        
        Returns:
            Dictionary with zone support configuration
        """
        if cls._config_cache is not None:
            return cls._config_cache
        
        try:
            with open(cls._config_path, 'r') as f:
                cls._config_cache = yaml.safe_load(f)
            return cls._config_cache
        except Exception as e:
            # Fallback to minimal configuration if file not found
            return {
                "no_zone_support": [],
                "zone_redundant_by_default": [],
                "zone_support_available": [],
                "zone_redundant_sku_patterns": [r".*ZRS$", r".*GZRS$"],
                "zone_redundant_properties": ["zoneRedundant"]
            }
    
    @classmethod
    def get_non_zonal_types(cls) -> Set[str]:
        """Get set of resource types that don't support zones."""
        config = cls._load_config()
        return set(config.get("no_zone_support", []))    
    @classmethod
    def get_zone_enabled_regions(cls) -> Set[str]:
        """Get the set of Azure regions that support availability zones."""
        config = cls._load_config()
        regions = config.get("zone_enabled_regions", [])
        # Normalize to lowercase for case-insensitive comparison
        return {region.lower().replace(' ', '') for region in regions}
    
    @classmethod
    def is_region_zone_capable(cls, location: str) -> bool:
        """Check if a region supports availability zones.
        
        Args:
            location: Azure region name (e.g., 'eastus', 'westeurope')
            
        Returns:
            True if the region supports availability zones
        """
        if not location:
            return False
        
        # Normalize location (remove spaces, lowercase)
        normalized_location = location.lower().replace(' ', '')
        zone_enabled_regions = cls.get_zone_enabled_regions()
        
        is_capable = normalized_location in zone_enabled_regions
        if not is_capable:
            logger.debug(f"Region '{location}' does not support availability zones")
        
        return is_capable    
    @classmethod
    def get_zone_redundant_patterns(cls) -> List[str]:
        """Get list of regex patterns for zone-redundant SKUs."""
        config = cls._load_config()
        return config.get("zone_redundant_sku_patterns", [r".*ZRS$", r".*GZRS$"])
    
    @staticmethod
    def extract_zonal_data(resource: Dict[str, Any]) -> ZonalData:
        """
        Extract zone configuration from an Azure resource.
        
        Analyzes resource properties to determine:
        - Which zones are used
        - Whether the resource is zone-redundant
        - Compliance with 3-AZ requirements
        
        Args:
            resource: Azure resource dictionary from Resource Graph
            
        Returns:
            ZonalData object with zone configuration details
            
        Examples:
            >>> resource = {
            ...     "type": "Microsoft.Compute/virtualMachines",
            ...     "properties": {"zones": ["1", "2", "3"]}
            ... }
            >>> data = ZonalAnalyzer.extract_zonal_data(resource)
            >>> data.meets_3az_requirement
            True
        """
        properties = resource.get("properties", {})
        resource_type_raw = resource.get("type") or properties.get("type") or ""
        resource_type = str(resource_type_raw).lower()
        location_raw = resource.get("location") or properties.get("location") or ""
        location = str(location_raw).lower()
        sku = resource.get("sku", {})
        sku_name = sku.get("name", "") if isinstance(sku, dict) else ""
        
        # FIRST CHECK: Does the region support availability zones?
        if location and not ZonalAnalyzer.is_region_zone_capable(location):
            return ZonalData(
                zones_used=[],
                is_zone_redundant=False,
                zone_count=0,
                meets_3az_requirement=True,  # N/A resources are considered compliant
                deployment_pattern=DeploymentPattern.NOT_APPLICABLE,
                recommendation=f"N/A - Region '{location}' does not support availability zones",
            )
        
        # SECOND CHECK: Does the resource type support zones?
        non_zonal_types = ZonalAnalyzer.get_non_zonal_types()
        if resource_type in non_zonal_types:
            return ZonalData(
                zones_used=[],
                is_zone_redundant=False,
                zone_count=0,
                meets_3az_requirement=True,  # N/A resources are considered compliant
                deployment_pattern=DeploymentPattern.NOT_APPLICABLE,
                recommendation="N/A - This resource type does not support availability zones",
            )
        
        # THIRD CHECK: SQL Servers are logical containers; zone config is at database level
        # VNets and subnets are regional resources that inherently span all zones
        # No configuration option exists to make them zone-specific
        if resource_type in ["microsoft.network/virtualnetworks", "microsoft.network/virtualnetworks/subnets"]:
            return ZonalData(
                zones_used=[],
                is_zone_redundant=False,
                zone_count=0,
                meets_3az_requirement=True,
                deployment_pattern=DeploymentPattern.NOT_APPLICABLE,
                recommendation="N/A - VNets and subnets are regional resources that automatically span all availability zones.",
            )
        
        # SQL Server is a logical container - zone resilience configured at database level
        if resource_type == "microsoft.sql/servers":
            return ZonalData(
                zones_used=[],
                is_zone_redundant=False,
                zone_count=0,
                meets_3az_requirement=True,
                deployment_pattern=DeploymentPattern.NOT_APPLICABLE,
                recommendation="N/A - SQL Server is a logical container. Zone resilience is configured at the database level.",
            )
        
        # Check zones property (most common)
        zones = properties.get("zones", [])
        if not zones:
            # Some resources store zones at the top level
            zones = resource.get("zones", [])
        
        # Normalize zones to list of strings
        if isinstance(zones, str):
            zones = [zones]
        zones = [str(z) for z in zones] if zones else []
        
        # Special handling for AKS: Check node pool availability zones
        if resource_type == "microsoft.containerservice/managedclusters" and not zones:
            # Check default_node_pool.availability_zones
            default_pool = properties.get("default_node_pool", {})
            if default_pool and isinstance(default_pool, dict):
                zones = default_pool.get("availability_zones", [])
            
            # Fallback: Check nodePoolProfiles (Azure API response format)
            if not zones:
                node_pools = properties.get("nodePoolProfiles", [])
                if node_pools and isinstance(node_pools, list) and len(node_pools) > 0:
                    zones = node_pools[0].get("availabilityZones", [])
            
            # Normalize to list of strings
            if isinstance(zones, str):
                zones = [zones]
            zones = [str(z) for z in zones] if zones else []
        
        # Check if resource is inherently zone-redundant by SKU/type
        is_zone_redundant = ZonalAnalyzer._is_zone_redundant_sku(sku_name, properties)
        
        # For resources with explicit zone_redundant property (e.g., SQL Database),
        # if not zone-redundant and no zones specified, mark as single-zone
        has_explicit_zone_redundant = "zone_redundant" in properties or "zoneRedundant" in properties
        
        # Check for storage account replication types
        replication_type = properties.get("account_replication_type", "")
        
        # For storage accounts, also check SKU name (e.g., "Standard_LRS", "Standard_GRS", "Standard_ZRS")
        if not replication_type and resource_type == "microsoft.storage/storageaccounts" and sku_name:
            # Extract replication type from SKU name (e.g., "Standard_LRS" -> "LRS")
            sku_parts = sku_name.split("_")
            if len(sku_parts) >= 2:
                replication_type = sku_parts[-1]  # Get the last part after underscore
        
        is_lrs = replication_type.upper() == "LRS"  # LRS = Locally Redundant = Single Zone
        
        # Determine deployment pattern
        if is_zone_redundant:
            pattern = DeploymentPattern.ZONE_REDUNDANT
            zone_count = 0  # Zone-redundant resources don't expose specific zones
            meets_3az = True
            
        elif len(zones) >= 3:
            pattern = DeploymentPattern.MULTI_ZONE
            zone_count = len(zones)
            meets_3az = True
            
        elif len(zones) == 2:
            pattern = DeploymentPattern.MULTI_ZONE
            zone_count = 2
            meets_3az = False
            
        elif len(zones) == 1:
            pattern = DeploymentPattern.SINGLE_ZONE
            zone_count = 1
            meets_3az = False
        
        elif has_explicit_zone_redundant and not is_zone_redundant:
            # Resource has explicit zone_redundant=false property, so it's single-zone
            pattern = DeploymentPattern.SINGLE_ZONE
            zone_count = 1
            meets_3az = False
            zones = ["unknown"]  # Mark that it's single-zone but zone not specified
        
        elif is_lrs:
            # Storage account with LRS (Locally Redundant Storage) = Single Zone
            pattern = DeploymentPattern.SINGLE_ZONE
            zone_count = 1
            meets_3az = False
            zones = ["unknown"]  # Mark that it's single-zone but zone not specified
            
        else:
            # No zone information available
            pattern = DeploymentPattern.UNKNOWN
            zone_count = 0
            meets_3az = False
        
        # Generate recommendation using config-driven engine
        engine = ZoneRecommendationEngine()
        zone_rec = engine.get_recommendation(
            resource_type=resource_type,
            deployment_pattern=pattern,
            zone_count=zone_count,
            zones_used=zones
        )
        recommendation = zone_rec.to_short_text(zone_count=zone_count, zones_used=zones)
        
        return ZonalData(
            zones_used=zones,
            is_zone_redundant=is_zone_redundant,
            zone_count=zone_count,
            meets_3az_requirement=meets_3az,
            deployment_pattern=pattern,
            recommendation=recommendation,
        )
    
    @staticmethod
    def analyze_with_correlation(
        resource: Dict[str, Any],
        correlator: 'ResourceCorrelator',  # Avoid circular import
        all_resources: List[Dict[str, Any]] = None
    ) -> ZonalData:
        """
        Analyze resource zone configuration with resilience group context.
        
        When a resource is part of a resilience group (e.g., VM in an Availability Set),
        this analyzes the group's resilience rather than the individual resource.
        
        Args:
            resource: Azure resource dictionary
            correlator: ResourceCorrelator instance for group lookups
            all_resources: Optional list of all resources for analysis
            
        Returns:
            ZonalData with group context applied
        """
        # Get basic analysis for the resource
        zonal_data = ZonalAnalyzer.extract_zonal_data(resource)
        
        # Check if resource is part of a resilience group
        group = correlator.get_group_for_resource(resource.get('id'))
        
        if not group:
            return zonal_data  # No group context
        
        # For Availability Sets and VMSS: Analyze group members collectively
        if group.type.value in ['availability_set', 'vmss']:
            return ZonalAnalyzer._analyze_group_resilience(group, correlator, resource)
        
        # For Load Balancer backend: Check if LB is zone-redundant
        if group.type.value == 'load_balancer_backend':
            return ZonalAnalyzer._analyze_lb_protected_resource(group, resource, correlator)
        
        # For replicated resources: Check replication
        if group.type.value in ['replicated_resource', 'cosmos_replicated', 'database_failover']:
            return ZonalAnalyzer._analyze_replicated_resource(group, resource, correlator)
        
        return zonal_data
    
    @staticmethod
    def _analyze_group_resilience(
        group: 'ResiliencyGroup',
        correlator: 'ResourceCorrelator',
        resource: Dict[str, Any]
    ) -> ZonalData:
        """
        Analyze zone resilience at the group level (Availability Set or VMSS).
        
        Collects zones from all group members to determine effective resilience.
        """
        from app.resilience.resilience_correlator import ResiliencyGroupType
        
        # Get all members of the group
        members = correlator.get_group_members(group.id)
        
        # Collect zones from all members
        all_zones: Set[str] = set()
        member_count = 0
        
        for member in members:
            member_zones = member.get('zones', [])
            if isinstance(member_zones, list):
                all_zones.update(str(z) for z in member_zones)
            member_count += 1
        
        zones_used = sorted(list(all_zones))
        zone_count = len(zones_used)
        
        # Determine pattern
        if zone_count >= 3:
            pattern = DeploymentPattern.MULTI_ZONE
            meets_3az = True
            recommendation = (
                f"✓ {group.name} spans {zone_count} zones with {member_count} members. "
                f"Zones: {', '.join(zones_used)}. Multi-zone resilience achieved."
            )
        elif zone_count == 2:
            pattern = DeploymentPattern.MULTI_ZONE
            meets_3az = False
            recommendation = (
                f"⚠ {group.name} spans {zone_count} zones with {member_count} members. "
                f"Zones: {', '.join(zones_used)}. Consider adding a third zone."
            )
        elif zone_count == 1:
            pattern = DeploymentPattern.SINGLE_ZONE
            meets_3az = False
            recommendation = (
                f"✗ {group.name} is in single zone {zones_used[0]} with {member_count} members. "
                f"Group should span multiple zones for resilience."
            )
        else:
            pattern = DeploymentPattern.UNKNOWN
            meets_3az = False
            recommendation = f"? {group.name} zone configuration unknown"
        
        # Individual resource context
        resource_zone = resource.get('zones', [])[0] if resource.get('zones') else 'N/A'
        recommendation += f"\n(This {resource.get('type', 'resource').split('/')[-1]} is in zone {resource_zone})"
        
        return ZonalData(
            zones_used=zones_used,
            is_zone_redundant=False,  # AS doesn't provide automatic redundancy
            zone_count=zone_count,
            meets_3az_requirement=meets_3az,
            deployment_pattern=pattern,
            recommendation=recommendation
        )
    
    @staticmethod
    def _analyze_lb_protected_resource(
        group: 'ResiliencyGroup',
        resource: Dict[str, Any],
        correlator: 'ResourceCorrelator'
    ) -> ZonalData:
        """
        Analyze resource protected by a Load Balancer.
        
        If the LB is zone-redundant, mark the resource as having resilience protection.
        """
        is_zone_redundant = group.metadata.get('is_zone_redundant', False)
        lb_name = group.metadata.get('load_balancer_name', group.id)
        
        if is_zone_redundant:
            recommendation = (
                f"✓ Protected by zone-redundant Load Balancer '{lb_name}'. "
                f"Resource is in zone {resource.get('zones', ['N/A'])[0]}. "
                f"LB provides AZ resilience for traffic distribution."
            )
            return ZonalData(
                zones_used=[],
                is_zone_redundant=True,
                zone_count=0,
                meets_3az_requirement=True,
                deployment_pattern=DeploymentPattern.ZONE_REDUNDANT,
                recommendation=recommendation
            )
        else:
            resource_zone = resource.get('zones', ['N/A'])[0]
            recommendation = (
                f"⚠ Behind Load Balancer '{lb_name}' (not zone-redundant). "
                f"Resource is in zone {resource_zone}. "
                f"Consider using zone-redundant LB SKU for improved resilience."
            )
            return ZonalData(
                zones_used=[resource_zone],
                is_zone_redundant=False,
                zone_count=1,
                meets_3az_requirement=False,
                deployment_pattern=DeploymentPattern.SINGLE_ZONE,
                recommendation=recommendation
            )
    
    @staticmethod
    def _analyze_replicated_resource(
        group: 'ResiliencyGroup',
        resource: Dict[str, Any],
        correlator: 'ResourceCorrelator'
    ) -> ZonalData:
        """
        Analyze multi-region/multi-zone replicated resources.
        
        For resources with replication (Cosmos, SQL Failover, Storage GRS), determine
        if replication provides adequate resilience.
        """
        metadata = group.metadata or {}
        replica_regions = metadata.get('replica_regions', [])
        read_regions = metadata.get('read_regions', [])
        zone_redundant = metadata.get('zone_redundant', False)
        
        region_count = len(replica_regions) + len(read_regions)
        
        if region_count >= 2:
            pattern = DeploymentPattern.ZONE_REDUNDANT  # Multi-region = inherent redundancy
            meets_3az = True
            recommendation = (
                f"✓ {group.name} is replicated across {region_count} regions. "
                f"Regions: {', '.join(replica_regions or read_regions)}. "
                f"Multi-region replication provides automatic resilience."
            )
        else:
            pattern = DeploymentPattern.SINGLE_ZONE
            meets_3az = zone_redundant
            recommendation = (
                f"⚠ {group.name} has limited replication. "
                f"Consider enabling multi-region replication for disaster recovery."
            )
        
        return ZonalData(
            zones_used=[],
            is_zone_redundant=True,
            zone_count=0,
            meets_3az_requirement=meets_3az,
            deployment_pattern=pattern,
            recommendation=recommendation
        )
    
    @staticmethod
    def _analyze_vnet_zone_architecture(resource: Dict[str, Any], location: str) -> ZonalData:
        """
        Analyze Virtual Network for zone resilience capability.
        
        VNets are regional resources that automatically span all availability zones
        in the region. Subnets within a VNet also span all zones - resources deployed
        in a subnet can be placed in any zone.
        
        This check verifies:
        - VNet is in a zone-enabled region
        - VNet has at least one subnet (can host resources)
        
        Note: The number of subnets does NOT affect zone distribution capability.
        Multiple subnets are for network segmentation (tiers, security boundaries),
        not for zone distribution. A single subnet can host resources across all zones.
        
        Args:
            resource: VNet resource dictionary
            location: Azure region
            
        Returns:
            ZonalData indicating if VNet is zone-capable
        """
        properties = resource.get("properties", {})
        subnets = properties.get("subnets", [])
        subnet_count = len(subnets)
        
        if subnet_count == 0:
            # VNet with no subnets cannot host resources
            return ZonalData(
                zones_used=[],
                is_zone_redundant=False,
                zone_count=0,
                meets_3az_requirement=False,
                deployment_pattern=DeploymentPattern.UNKNOWN,
                recommendation="⚠ VNet has no subnets configured. Add at least one subnet to enable resource deployment.",
            )
        
        # VNet in zone-enabled region with subnets is fully zone-capable
        # Subnets span all zones - resources within can use any zone
        return ZonalData(
            zones_used=[],
            is_zone_redundant=True,  # Infrastructure spans all zones
            zone_count=3,  # VNet/subnets span all 3 zones in the region
            meets_3az_requirement=True,
            deployment_pattern=DeploymentPattern.ZONE_REDUNDANT,
            recommendation=f"✓ VNet in zone-enabled region '{location}' with {subnet_count} subnet(s). Subnets span all availability zones. Deploy resources across zones 1, 2, and 3 for resilience.",
        )
    
    @staticmethod
    def _is_zone_redundant_sku(sku_name: str, properties: Dict[str, Any]) -> bool:
        """
        Check if a SKU/configuration is zone-redundant by nature.
        
        Args:
            sku_name: SKU name (e.g., "Standard_ZRS", "Premium_ZRS")
            properties: Resource properties dictionary
            
        Returns:
            True if the resource is zone-redundant
        """
        if not sku_name:
            # For storage accounts, check account_replication_type
            replication_type = properties.get("account_replication_type", "")
            if replication_type:
                sku_name = replication_type
        
        if not sku_name:
            return False
        
        # Check against known zone-redundant patterns from config
        patterns = ZonalAnalyzer.get_zone_redundant_patterns()
        for pattern in patterns:
            if re.match(pattern, sku_name, re.IGNORECASE):
                return True
        
        # Check for explicit zone redundancy in properties (handle both camelCase and snake_case)
        zone_redundant_prop = properties.get("zoneRedundant") or properties.get("zone_redundant")
        if zone_redundant_prop:
            return True
        
        return False


class ZonalResiliencySummary:
    """
    Summary statistics for zone configuration across multiple resources.
    
    Provides aggregate metrics:
    - Total resources analyzed
    - Count by deployment pattern
    - Overall 3-AZ compliance
    - Resiliency score
    - Regional distribution analysis
    
    Usage:
        all_zonal = [ZonalAnalyzer.extract_zonal_data(r) for r in resources]
        summary = ZonalResiliencySummary(all_zonal, resources)
        print(f"3-AZ Compliant: {summary.is_3az_compliant}")
    """
    
    def __init__(self, zonal_data_list: List[ZonalData], resources: List[Dict[str, Any]] = None):
        """
        Initialize summary from list of ZonalData objects.
        
        Args:
            zonal_data_list: List of ZonalData from analyzed resources
            resources: Optional list of original resource dictionaries for region analysis
        """
        self.total_resources = len(zonal_data_list)
        
        # Count by deployment pattern
        self.zone_redundant_count = sum(
            1 for z in zonal_data_list 
            if z.deployment_pattern == DeploymentPattern.ZONE_REDUNDANT
        )
        self.multi_zone_count = sum(
            1 for z in zonal_data_list 
            if z.deployment_pattern == DeploymentPattern.MULTI_ZONE
        )
        self.single_zone_count = sum(
            1 for z in zonal_data_list 
            if z.deployment_pattern == DeploymentPattern.SINGLE_ZONE
        )
        self.not_applicable_count = sum(
            1 for z in zonal_data_list 
            if z.deployment_pattern == DeploymentPattern.NOT_APPLICABLE
        )
        self.unknown_count = sum(
            1 for z in zonal_data_list 
            if z.deployment_pattern == DeploymentPattern.UNKNOWN
        )
        
        # Count resources that meet 3-AZ requirement
        self.compliant_3az_count = sum(
            1 for z in zonal_data_list 
            if z.meets_3az_requirement
        )
        
        # Overall compliance (true if ALL resources meet 3-AZ requirement)
        self.is_3az_compliant = (
            self.compliant_3az_count == self.total_resources 
            if self.total_resources > 0 
            else False
        )
        
        # Region analysis (if resources provided)
        self.regions_analyzed = []
        self.zone_enabled_regions = []
        self.non_zone_regions = []
        self.resources_in_non_zone_regions = 0
        self.resources_in_zone_regions = 0
        
        if resources:
            self._analyze_regions(resources)
        
        # Calculate resilience score (0-100%)
        # Score = (zone_redundant * 1.0 + multi_zone * 0.8 + single_zone * 0.3) / total_applicable
        # Note: NOT_APPLICABLE resources are excluded from scoring
        total_applicable = self.total_resources - self.not_applicable_count
        
        if total_applicable > 0:
            weighted_score = (
                (self.zone_redundant_count * 1.0) +
                (self.multi_zone_count * 0.8) +
                (self.single_zone_count * 0.3)
            )
            self.zonal_resilience_score = weighted_score / total_applicable
        else:
            self.zonal_resilience_score = 0.0
    
    def _analyze_regions(self, resources: List[Dict[str, Any]]):
        """Analyze regional distribution of resources."""
        # Extract all regions
        regions = [r.get('location', '').lower() for r in resources if r.get('location')]
        self.regions_analyzed = sorted(set(regions))
        
        # Categorize regions
        for region in self.regions_analyzed:
            if ZonalAnalyzer.is_region_zone_capable(region):
                self.zone_enabled_regions.append(region)
            else:
                self.non_zone_regions.append(region)
        
        # Count resources by region type
        for resource in resources:
            location = resource.get('location', '').lower()
            if location:
                if ZonalAnalyzer.is_region_zone_capable(location):
                    self.resources_in_zone_regions += 1
                else:
                    self.resources_in_non_zone_regions += 1
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert summary to dictionary format for JSON serialization.
        
        Returns:
            Dictionary with all summary metrics including regional analysis
        """
        total_applicable = self.total_resources - self.not_applicable_count
        
        result = {
            "total_resources": self.total_resources,
            "zone_redundant_resources": self.zone_redundant_count,
            "multi_zone_resources": self.multi_zone_count,
            "single_zone_resources": self.single_zone_count,
            "not_applicable_resources": self.not_applicable_count,
            "unknown_zone_resources": self.unknown_count,
            "compliant_3az_resources": self.compliant_3az_count,
            "overall_3az_compliant": self.is_3az_compliant,
            "zonal_resilience_score": round(self.zonal_resilience_score, 4),
            "compliance_percentage": round(
                (self.compliant_3az_count / total_applicable * 100) 
                if total_applicable > 0 
                else 0, 
                2
            ),
        }
        
        # Add regional analysis if available
        if self.regions_analyzed:
            result["regional_analysis"] = {
                "total_regions": len(self.regions_analyzed),
                "zone_enabled_regions_count": len(self.zone_enabled_regions),
                "non_zone_regions_count": len(self.non_zone_regions),
                "resources_in_zone_regions": self.resources_in_zone_regions,
                "resources_in_non_zone_regions": self.resources_in_non_zone_regions,
                "regions": self.regions_analyzed,
                "zone_enabled_regions": self.zone_enabled_regions,
                "non_zone_regions": self.non_zone_regions,
            }
        
        return result
