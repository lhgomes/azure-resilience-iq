"""
Zone Recommendation Engine - Generic, Config-Driven System

This module provides a generic engine for generating zone resilience recommendations
based on YAML configuration files, following the APRL model:
  - Generic engine (this file)
  - Configuration files (zone_recommendations.yaml)
  - Clear separation of logic and data

Design Philosophy:
- Contributor-friendly: Non-developers can add recommendations via YAML
- Scalable: No code changes needed for new resource types
- Maintainable: Single source of truth for recommendations
- Consistent: Follows APRL's proven patterns

Usage:
    engine = ZoneRecommendationEngine()
    recommendation = engine.get_recommendation(
        resource_type="Microsoft.Compute/virtualMachines",
        deployment_pattern=DeploymentPattern.SINGLE_ZONE,
        zone_count=1,
        zones_used=["1"]
    )
"""

import os
import logging
from typing import Dict, Any, Optional, List
from pathlib import Path
import yaml
from dataclasses import dataclass
from enum import Enum

LOGGER = logging.getLogger(__name__)


class DeploymentPattern(Enum):
    """Azure Availability Zone deployment patterns."""
    ZONE_REDUNDANT = "zone_redundant"
    MULTI_ZONE = "multi_zone"
    SINGLE_ZONE = "single_zone"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


@dataclass
class ZoneRecommendation:
    """
    Structured recommendation for zone deployment, aligned with APRL format.
    
    Attributes:
        description: Short recommendation text (<150 chars)
        long_description: Detailed explanation with context
        potential_benefits: Benefits of implementing the recommendation
        recommendation_control: Category (HighAvailability, DisasterRecovery, etc.)
        recommendation_impact: High | Medium | Low
        learn_more_links: List of documentation URLs
        aprl_guid: Optional APRL reference GUID
    """
    description: str
    long_description: str
    potential_benefits: str
    recommendation_control: str
    recommendation_impact: str
    learn_more_links: List[Dict[str, str]]
    aprl_guid: Optional[str] = None
    
    def to_short_text(self, zone_count: int = 0, zones_used: List[str] = None) -> str:
        """
        Generate short recommendation text with context.
        
        Args:
            zone_count: Number of zones
            zones_used: List of zone identifiers
            
        Returns:
            Formatted short recommendation string
        """
        # Add zone context if available
        context = ""
        if zones_used and len(zones_used) > 0:
            zone_list = ", ".join(sorted(zones_used))
            context = f" (Current: zone{'s' if len(zones_used) > 1 else ''} {zone_list})"
        elif zone_count > 0:
            context = f" ({zone_count} zone{'s' if zone_count != 1 else ''})"
        
        return f"{self.description}{context}"
    
    def to_detailed_text(self) -> str:
        """Generate detailed recommendation with benefits and links."""
        text = f"{self.long_description}\n\n"
        text += f"Benefits: {self.potential_benefits}\n\n"
        
        if self.learn_more_links:
            text += "Learn more:\n"
            for link in self.learn_more_links:
                text += f"  - {link['name']}: {link['url']}\n"
        
        return text.strip()


class ZoneRecommendationEngine:
    """
    Generic engine for zone resilience recommendations.
    
    Loads recommendations from YAML config and provides lookup based on
    resource type and deployment pattern. Implements fallback hierarchy:
    1. Exact resource type match
    2. Generic fallback (*)
    3. Hardcoded default
    
    Example:
        >>> engine = ZoneRecommendationEngine()
        >>> rec = engine.get_recommendation(
        ...     resource_type="Microsoft.Compute/virtualMachines",
        ...     deployment_pattern=DeploymentPattern.SINGLE_ZONE
        ... )
        >>> print(rec.description)
        "Deploy VMs using VMSS Flex across availability zones"
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize recommendation engine.
        
        Args:
            config_path: Path to resilience_rules_extra directory (optional)
                        Defaults to backend/config/resilience_rules_extra
                        
        The engine loads recommendations from APRL-aligned directory structure:
        - backend/config/resilience_rules_extra/Compute/virtualMachines/zone_recommendations.yaml
        - backend/config/resilience_rules_extra/Network/publicIPAddresses/zone_recommendations.yaml
        - etc.
        """
        self.config_path = config_path or self._get_default_config_path()
        self.recommendations: Dict[str, Dict[str, ZoneRecommendation]] = {}
        self.load_config()
    
    @staticmethod
    def _get_default_config_path() -> str:
        """Get default config directory path (backend/config/resilience_rules_extra)."""
        # Navigate from current file location to backend/config/resilience_rules_extra
        current_file = Path(__file__)
        backend_dir = current_file.parent.parent.parent  # app/resilience -> backend
        config_dir = backend_dir / "config" / "resilience_rules_extra"
        return str(config_dir)
    
    def load_config(self) -> None:
        """
        Load recommendations from APRL-aligned directory structure.
        
        Loads all zone_recommendations.yaml files from:
        - backend/config/resilience_rules_extra/Compute/*/zone_recommendations.yaml
        - backend/config/resilience_rules_extra/Network/*/zone_recommendations.yaml
        - backend/config/resilience_rules_extra/*/*/zone_recommendations.yaml
        
        Parses YAML into internal recommendation lookup structure:
        {
            "Microsoft.Compute/virtualMachines": {
                "single_zone": ZoneRecommendation(...),
                "multi_zone_2": ZoneRecommendation(...),
                ...
            },
            ...
        }
        
        Raises:
            FileNotFoundError: If config directory doesn't exist
            yaml.YAMLError: If YAML is malformed
        """
        if not os.path.exists(self.config_path):
            LOGGER.warning(f"Zone recommendations config directory not found: {self.config_path}")
            LOGGER.warning("Using hardcoded fallback recommendations")
            self._load_hardcoded_fallback()
            return
        
        config_dir = Path(self.config_path)
        yaml_files = list(config_dir.glob("**/zone_recommendations.yaml"))
        
        if not yaml_files:
            LOGGER.warning(f"No zone_recommendations.yaml files found in {self.config_path}")
            LOGGER.warning("Using hardcoded fallback recommendations")
            self._load_hardcoded_fallback()
            return
        
        LOGGER.debug(f"Found {len(yaml_files)} zone recommendation files in {self.config_path}")
        
        files_loaded = 0
        files_failed = 0
        
        for yaml_file in sorted(yaml_files):
            try:
                self._load_yaml_file(yaml_file)
                files_loaded += 1
                LOGGER.debug(f"Loaded: {yaml_file.relative_to(config_dir)}")
            except Exception as e:
                files_failed += 1
                LOGGER.error(f"Error loading {yaml_file.relative_to(config_dir)}: {e}")
                continue
        
        if self.recommendations:
            LOGGER.debug(
                f"✓ Loaded zone recommendations: {files_loaded} files, "
                f"{len(self.recommendations)} resource types"
            )
            LOGGER.debug(f"Loaded resource types: {sorted(self.recommendations.keys())}")
        else:
            LOGGER.warning("No valid recommendations loaded, using hardcoded fallback")
            self._load_hardcoded_fallback()
        
        if files_failed > 0:
            LOGGER.warning(f"⚠ {files_failed} recommendation files failed to load")
    
    def _load_yaml_file(self, yaml_file: Path) -> None:
        """
        Load a single zone_recommendations.yaml file.
        
        Args:
            yaml_file: Path to the YAML file
            
        Raises:
            yaml.YAMLError: If YAML is malformed
            ValueError: If file structure is invalid
        """
        with open(yaml_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Split by --- to separate APRL recommendations from pattern mappings
        sections = content.split('---')
        
        # The patterns section is after the second --- or at the end
        patterns_data = {}
        
        for section in sections:
            section = section.strip()
            if not section:
                continue
            
            try:
                data = yaml.safe_load(section)
            except yaml.YAMLError as e:
                LOGGER.error(f"YAML parse error in {yaml_file}: {e}")
                raise
            
            if not data:
                continue
            
            # Handle patterns section
            if isinstance(data, dict) and 'patterns' in data:
                patterns_data = data.get('patterns', {})
            # Handle recommendations list (APRL format)
            elif isinstance(data, list):
                # Skip APRL-format recommendations for now
                # They serve as documentation/reference
                continue
        
        # If no patterns found, file might be APRL-only documentation
        if not patterns_data:
            LOGGER.debug(f"No pattern mappings found in {yaml_file}")
            return
        
        # Infer resource type from file path
        # e.g., Compute/virtualMachines/zone_recommendations.yaml
        # -> Microsoft.Compute/virtualMachines
        resource_type = self._infer_resource_type(yaml_file)
        
        if not resource_type:
            LOGGER.warning(f"Could not infer resource type from path: {yaml_file}")
            return
        
        # Normalize to lowercase for consistent lookups
        resource_type = resource_type.lower()
        
        # Parse patterns into recommendations
        self.recommendations[resource_type] = {}
        
        for pattern_key, pattern_data in patterns_data.items():
            try:
                recommendation = ZoneRecommendation(
                    description=pattern_data.get('description', 'Review zone configuration'),
                    long_description=pattern_data.get('longDescription', ''),
                    potential_benefits=pattern_data.get('potentialBenefits', ''),
                    recommendation_control=pattern_data.get('recommendationControl', 'HighAvailability'),
                    recommendation_impact=pattern_data.get('recommendationImpact', 'Medium'),
                    learn_more_links=pattern_data.get('learnMoreLink', []),
                    aprl_guid=pattern_data.get('aprlGuid')
                )
                self.recommendations[resource_type][pattern_key] = recommendation
            except Exception as e:
                LOGGER.error(f"Error parsing pattern {pattern_key} in {yaml_file}: {e}")
                continue
    
    def _infer_resource_type(self, yaml_file: Path) -> Optional[str]:
        """
        Infer Azure resource type from file path.
        
        Examples:
        - Compute/virtualMachines/zone_recommendations.yaml 
          -> Microsoft.Compute/virtualMachines
        - Network/publicIPAddresses/zone_recommendations.yaml 
          -> Microsoft.Network/publicIPAddresses
        - Storage/storageAccounts/zone_recommendations.yaml 
          -> Microsoft.Storage/storageAccounts
        
        Args:
            yaml_file: Path to zone_recommendations.yaml
            
        Returns:
            Azure resource type string or None if cannot be inferred
        """
        try:
            # Get path relative to config root, excluding the yaml filename
            # Example: /backend/config/resilience_rules_extra/Compute/virtualMachines/zone_recommendations.yaml
            # We want: Compute/virtualMachines
            config_root = Path(self.config_path)
            relative_path = yaml_file.relative_to(config_root)
            
            # Remove the filename to get directory path
            dir_parts = relative_path.parent.parts  # ['Compute', 'virtualMachines']
            
            if len(dir_parts) >= 2:
                service = dir_parts[0]  # e.g., "Compute", "Network"
                resource = '/'.join(dir_parts[1:])  # e.g., "virtualMachines", or "servers/databases"
                
                # Map service names to Azure namespaces
                service_map = {
                    'Compute': 'Microsoft.Compute',
                    'Network': 'Microsoft.Network',
                    'Storage': 'Microsoft.Storage',
                    'Sql': 'Microsoft.Sql',
                    'DocumentDB': 'Microsoft.DocumentDB',
                    'ContainerRegistry': 'Microsoft.ContainerRegistry',
                    'KeyVault': 'Microsoft.KeyVault',
                    'Database': 'Microsoft.DBforPostgreSQL',  # flexible servers
                }
                
                namespace = service_map.get(service, f'Microsoft.{service}')
                return f"{namespace}/{resource}"
        except Exception as e:
            LOGGER.debug(f"Could not infer resource type from {yaml_file}: {e}")
        
        return None
    
    def _load_hardcoded_fallback(self) -> None:
        """Load minimal hardcoded recommendations as fallback."""
        LOGGER.info("Loading hardcoded fallback recommendations")
        
        # Generic fallback for any resource type
        self.recommendations["*"] = {
            "single_zone": ZoneRecommendation(
                description="Consider zone-redundant or multi-zone deployment",
                long_description="Resource is in a single zone. Consider multi-zone deployment for resilience.",
                potential_benefits="Improved availability",
                recommendation_control="HighAvailability",
                recommendation_impact="Medium",
                learn_more_links=[{
                    "name": "Availability zones",
                    "url": "https://learn.microsoft.com/azure/reliability/availability-zones-overview"
                }]
            ),
            "multi_zone_2": ZoneRecommendation(
                description="Add third availability zone",
                long_description="Resource spans 2 zones. Consider adding 3rd zone for full resilience.",
                potential_benefits="Complete zone redundancy",
                recommendation_control="HighAvailability",
                recommendation_impact="Low",
                learn_more_links=[]
            ),
            "multi_zone_3plus": ZoneRecommendation(
                description="Resource meets 3-AZ requirement",
                long_description="Resource is distributed across 3+ zones.",
                potential_benefits="Maximum availability",
                recommendation_control="HighAvailability",
                recommendation_impact="Low",
                learn_more_links=[]
            ),
            "zone_redundant": ZoneRecommendation(
                description="Zone-redundant configuration active",
                long_description="Resource uses zone-redundant configuration.",
                potential_benefits="Automatic zone failover",
                recommendation_control="HighAvailability",
                recommendation_impact="Low",
                learn_more_links=[]
            ),
            "not_applicable": ZoneRecommendation(
                description="N/A - Resource type does not support availability zones",
                long_description="This resource type does not support zones.",
                potential_benefits="N/A",
                recommendation_control="HighAvailability",
                recommendation_impact="Low",
                learn_more_links=[]
            ),
            "unknown": ZoneRecommendation(
                description="Verify zone configuration",
                long_description="Zone configuration unknown. Review in Azure Portal.",
                potential_benefits="Clarity on resilience",
                recommendation_control="HighAvailability",
                recommendation_impact="Medium",
                learn_more_links=[]
            ),
        }
    
    def _get_pattern_key(self, deployment_pattern: DeploymentPattern, zone_count: int) -> str:
        """
        Map deployment pattern and zone count to config pattern key.
        
        Args:
            deployment_pattern: DeploymentPattern enum value
            zone_count: Number of zones
            
        Returns:
            Pattern key string (e.g., "single_zone", "multi_zone_3plus")
        """
        if deployment_pattern == DeploymentPattern.SINGLE_ZONE:
            return "single_zone"
        elif deployment_pattern == DeploymentPattern.MULTI_ZONE:
            if zone_count == 2:
                return "multi_zone_2"
            elif zone_count >= 3:
                return "multi_zone_3plus"
            else:
                return "single_zone"  # Fallback
        elif deployment_pattern == DeploymentPattern.ZONE_REDUNDANT:
            return "zone_redundant"
        elif deployment_pattern == DeploymentPattern.NOT_APPLICABLE:
            return "not_applicable"
        elif deployment_pattern == DeploymentPattern.UNKNOWN:
            return "unknown"
        else:
            return "unknown"
    
    def get_recommendation(
        self,
        resource_type: str,
        deployment_pattern: DeploymentPattern,
        zone_count: int = 0,
        zones_used: Optional[List[str]] = None
    ) -> ZoneRecommendation:
        """
        Get zone recommendation for a resource.
        
        Implements fallback hierarchy:
        1. Exact resource type match
        2. Generic fallback (resourceType: "*")
        3. Hardcoded default
        
        Args:
            resource_type: Azure resource type (e.g., "Microsoft.Compute/virtualMachines")
            deployment_pattern: DeploymentPattern enum
            zone_count: Number of zones (for pattern resolution)
            zones_used: List of zone IDs (optional, for context)
            
        Returns:
            ZoneRecommendation object with structured guidance
            
        Example:
            >>> engine = ZoneRecommendationEngine()
            >>> rec = engine.get_recommendation(
            ...     "Microsoft.Compute/virtualMachines",
            ...     DeploymentPattern.SINGLE_ZONE,
            ...     zone_count=1,
            ...     zones_used=["1"]
            ... )
            >>> rec.description
            "Deploy VMs using VMSS Flex across availability zones"
        """
        # Normalize resource type
        resource_type_normalized = resource_type.lower()
        
        # Get pattern key
        pattern_key = self._get_pattern_key(deployment_pattern, zone_count)
        
        # Try exact resource type match
        for rt_key, patterns in self.recommendations.items():
            if rt_key.lower() == resource_type_normalized:
                if pattern_key in patterns:
                    LOGGER.debug(f"Found recommendation: {resource_type} -> {pattern_key}")
                    return patterns[pattern_key]
                # Pattern not found for this resource type, try single_zone as fallback
                # (most resources have single_zone pattern defined)
                if 'single_zone' in patterns and pattern_key == 'unknown':
                    LOGGER.debug(f"Using single_zone fallback for {resource_type} -> {pattern_key}")
                    return patterns['single_zone']
        
        # Fallback to generic (*) recommendations
        if "*" in self.recommendations:
            generic_patterns = self.recommendations["*"]
            if pattern_key in generic_patterns:
                LOGGER.debug(f"Using generic recommendation for {resource_type} -> {pattern_key}")
                return generic_patterns[pattern_key]
        
        # Final fallback: hardcoded default
        # Use DEBUG level - these are expected for child resources and types without zone files
        LOGGER.debug(f"No recommendation found for {resource_type} -> {pattern_key}, using default")
        return ZoneRecommendation(
            description="Review zone configuration",
            long_description="No specific recommendation available for this resource type and pattern.",
            potential_benefits="Follow Azure best practices",
            recommendation_control="HighAvailability",
            recommendation_impact="Medium",
            learn_more_links=[{
                "name": "Azure availability zones",
                "url": "https://learn.microsoft.com/azure/reliability/availability-zones-overview"
            }]
        )
    
    def get_all_resource_types(self) -> List[str]:
        """Get list of all configured resource types."""
        return list(self.recommendations.keys())
    
    def reload_config(self) -> None:
        """Reload configuration from file (useful for testing/hot-reload)."""
        self.recommendations.clear()
        self.load_config()
