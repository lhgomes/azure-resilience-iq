"""
Load Balancer zone configuration analyzer.

Analyzes Load Balancer and Application Gateway zone resilience based on:
1. SKU and direct zones property
2. Frontend IP configuration (internal vs public)
3. Public IP zones (for public frontends)

This module derives zone resilience metadata WITHOUT modifying the zones field.
All analysis is based on existing properties and relationships.
"""

from typing import Dict, Any, List, Optional, Set
from dataclasses import dataclass


@dataclass
class LBZoneAnalysis:
    """Zone analysis result for a load balancer"""
    resource_id: str
    resource_name: str
    resource_type: str
    sku_name: str
    sku_tier: str
    direct_zones: Optional[List[str]]  # zones property from LB directly
    is_zone_redundant: bool  # Derived classification
    frontend_type: str  # 'internal' | 'public' | 'mixed'
    public_ips: List[Dict[str, Any]]  # List of public IPs with their zone info
    public_ip_zones_mismatch: bool  # LB zone-redundant but public IP is zonal
    classification: str  # 'zone_redundant' | 'zonal' | 'not_zone_resilient'
    recommendation: str


class LoadBalancerAnalyzer:
    """Analyzes Load Balancer and Application Gateway zone configuration."""

    @staticmethod
    def _extract_sku(resource: Dict[str, Any]) -> tuple:
        sku = resource.get('sku')
        if not sku:
            properties = resource.get('properties') or {}
            if isinstance(properties, dict):
                sku = properties.get('sku', {})

        if not isinstance(sku, dict):
            return '', ''

        return sku.get('name', ''), sku.get('tier', '')
    
    @staticmethod
    def analyze_load_balancer(
        resource: Dict[str, Any],
        all_resources_map: Dict[str, Dict[str, Any]]
    ) -> LBZoneAnalysis:
        """
        Analyze Load Balancer zone configuration from its properties and frontend config.
        
        Args:
            resource: Load Balancer resource from resources.json
            all_resources_map: Map of resource_id -> resource for lookups
            
        Returns:
            LBZoneAnalysis with zone classification and recommendations
        """
        resource_id = resource.get('id', '')
        resource_name = resource.get('name', '')
        resource_type = resource.get('type', '')
        
        # Step 1: Get SKU info (fallback to properties.sku)
        sku_name, sku_tier = LoadBalancerAnalyzer._extract_sku(resource)
        
        # Step 2: Check direct zones property
        direct_zones = resource.get('zones')
        if isinstance(direct_zones, list):
            direct_zones = [str(z) for z in direct_zones]
        else:
            direct_zones = None
        
        # Step 3: Analyze frontends for public/internal and public IP zones
        frontend_type, public_ips = LoadBalancerAnalyzer._analyze_frontends(
            resource, all_resources_map
        )
        
        # Step 4: Classify zone resilience
        classification, is_zone_redundant = LoadBalancerAnalyzer._classify_zone_resilience(
            sku_name, direct_zones
        )
        
        # Step 5: Check for mismatches (LB zone-redundant but public IP is zonal)
        public_ip_zones_mismatch = LoadBalancerAnalyzer._check_public_ip_mismatch(
            is_zone_redundant, public_ips
        )
        
        # Step 6: Generate recommendation
        recommendation = LoadBalancerAnalyzer._generate_recommendation(
            classification, frontend_type, public_ips, public_ip_zones_mismatch
        )
        
        return LBZoneAnalysis(
            resource_id=resource_id,
            resource_name=resource_name,
            resource_type=resource_type,
            sku_name=sku_name,
            sku_tier=sku_tier,
            direct_zones=direct_zones,
            is_zone_redundant=is_zone_redundant,
            frontend_type=frontend_type,
            public_ips=public_ips,
            public_ip_zones_mismatch=public_ip_zones_mismatch,
            classification=classification,
            recommendation=recommendation
        )
    
    @staticmethod
    def _analyze_frontends(
        resource: Dict[str, Any],
        all_resources_map: Dict[str, Dict[str, Any]]
    ) -> tuple:
        """
        Analyze Load Balancer frontend configurations.
        
        Determines if LB is internal, public, or mixed.
        For public frontends, retrieves public IP zone information.
        
        Returns:
            (frontend_type, public_ips_list)
        """
        properties = resource.get('properties', {})
        frontend_configs = properties.get('frontendIPConfigurations', [])
        
        frontend_type = 'internal'  # Default
        public_ips = []
        
        if not isinstance(frontend_configs, list):
            return frontend_type, public_ips
        
        has_internal = False
        has_public = False
        
        for frontend in frontend_configs:
            if not isinstance(frontend, dict):
                continue
            
            frontend_props = frontend.get('properties', {})
            
            # Check for public IP reference
            public_ip_ref = frontend_props.get('publicIPAddress', {})
            if isinstance(public_ip_ref, dict) and 'id' in public_ip_ref:
                has_public = True
                public_ip_id = public_ip_ref.get('id', '').lower()
                
                # Look up the public IP resource
                public_ip = all_resources_map.get(public_ip_id)
                if public_ip:
                    public_ip_zones = public_ip.get('zones')
                    if isinstance(public_ip_zones, list):
                        public_ip_zones = [str(z) for z in public_ip_zones]
                    else:
                        public_ip_zones = None
                    
                    public_ips.append({
                        'id': public_ip.get('id', ''),
                        'name': public_ip.get('name', ''),
                        'zones': public_ip_zones,
                        'is_zone_redundant': public_ip_zones is None
                    })
            else:
                has_internal = True
        
        # Determine frontend type
        if has_public and has_internal:
            frontend_type = 'mixed'
        elif has_public:
            frontend_type = 'public'
        else:
            frontend_type = 'internal'
        
        return frontend_type, public_ips
    
    @staticmethod
    def _classify_zone_resilience(
        sku_name: str,
        direct_zones: Optional[List[str]]
    ) -> tuple:
        """
        Classify Load Balancer zone resilience based on SKU and zones property.
        
        Classification rules:
        - Zone-Redundant: sku.name == "Standard" AND zones == null
        - Zonal: sku.name == "Standard" AND zones contains "1"/"2"/"3"
        - Not Zone-Resilient: sku.name != "Standard"
        
        Returns:
            (classification, is_zone_redundant)
        """
        sku_name_upper = sku_name.upper() if sku_name else ''
        
        # Check SKU
        if sku_name_upper != 'STANDARD':
            return 'not_zone_resilient', False
        
        # Standard SKU: check zones
        if direct_zones is None:
            # No zones specified = zone-redundant (automatically spans zones)
            return 'zone_redundant', True
        elif isinstance(direct_zones, list) and len(direct_zones) > 0:
            # Explicit zones specified = zonal (single or multi-zone but explicitly configured)
            return 'zonal', False
        else:
            # Standard SKU but no zone info = treat as zone-redundant (Azure default for Standard)
            return 'zone_redundant', True
    
    @staticmethod
    def _check_public_ip_mismatch(
        is_zone_redundant: bool,
        public_ips: List[Dict[str, Any]]
    ) -> bool:
        """
        Check for zone resilience mismatch between LB and public IPs.
        
        Flag when:
        - LB is zone-redundant (zones == null)
        - BUT public IP is zonal (zones != null)
        
        Returns:
            True if mismatch detected
        """
        if not is_zone_redundant or not public_ips:
            return False
        
        # LB is zone-redundant; check if any public IP is zonal
        for public_ip in public_ips:
            if public_ip.get('zones') is not None:
                # This public IP is zonal while LB is zone-redundant
                return True
        
        return False
    
    @staticmethod
    def _generate_recommendation(
        classification: str,
        frontend_type: str,
        public_ips: List[Dict[str, Any]],
        public_ip_zones_mismatch: bool
    ) -> str:
        """Generate human-readable recommendation based on analysis."""
        
        if classification == 'not_zone_resilient':
            return "❌ Load Balancer is not zone-resilient (not Standard SKU). Upgrade to Standard SKU for zone resilience."
        
        if public_ip_zones_mismatch:
            public_ip_names = [ip.get('name', 'Unknown') for ip in public_ips]
            return f"⚠️  Mismatch: Load Balancer is zone-redundant, but public IP(s) {public_ip_names} are zonal. Consider using zone-redundant public IPs."
        
        if classification == 'zone_redundant':
            if frontend_type == 'internal':
                return "✓ Zone-redundant Load Balancer with internal frontend only."
            elif frontend_type == 'public':
                return "✓ Zone-redundant Load Balancer with zone-redundant public IP(s)."
            else:
                return "✓ Zone-redundant Load Balancer with mixed internal/public frontends."
        
        elif classification == 'zonal':
            return "⚠️  Load Balancer is zonal (deployed in specific availability zones). For 3-AZ resilience, ensure backend pool spans multiple zones."
        
        return "? Load Balancer zone configuration unknown."
    
    @staticmethod
    def analyze_application_gateway(
        resource: Dict[str, Any],
        all_resources_map: Dict[str, Dict[str, Any]]
    ) -> LBZoneAnalysis:
        """
        Analyze Application Gateway zone configuration.
        
        Similar to Load Balancer but with APGW-specific properties.
        """
        resource_id = resource.get('id', '')
        resource_name = resource.get('name', '')
        resource_type = resource.get('type', '')
        
        # Get SKU (fallback to properties.sku)
        sku_name, sku_tier = LoadBalancerAnalyzer._extract_sku(resource)
        
        # Check direct zones
        direct_zones = resource.get('zones')
        if isinstance(direct_zones, list):
            direct_zones = [str(z) for z in direct_zones]
        else:
            direct_zones = None
        
        # APGW: Check if zones are explicitly set
        # If zones property exists and has multiple zones → zone-redundant by Azure design
        # If zones is null → also zone-redundant (default for APGW v2)
        
        # Classify: APGW v2 is zone-aware
        if sku_name.upper() in ['STANDARD_V2', 'WAF_V2']:
            if direct_zones and len(direct_zones) >= 2:
                classification = 'zonal'
                is_zone_redundant = len(direct_zones) >= 3
            else:
                # APGW v2 defaults to zone-redundant
                classification = 'zone_redundant'
                is_zone_redundant = True
        else:
            classification = 'not_zone_resilient'
            is_zone_redundant = False
        
        # APGW doesn't use public IPs in same way as LB
        public_ips = []
        frontend_type = 'internal'
        public_ip_zones_mismatch = False
        
        # Generate recommendation
        if classification == 'not_zone_resilient':
            recommendation = "❌ Application Gateway is not zone-resilient. Upgrade to v2 SKU (Standard_v2 or WAF_v2)."
        elif classification == 'zone_redundant':
            recommendation = "✓ Application Gateway v2 is zone-redundant by default."
        else:
            recommendation = "⚠️  Application Gateway is configured for specific zones. Ensure backend pool spans multiple zones."
        
        return LBZoneAnalysis(
            resource_id=resource_id,
            resource_name=resource_name,
            resource_type=resource_type,
            sku_name=sku_name,
            sku_tier=sku_tier,
            direct_zones=direct_zones,
            is_zone_redundant=is_zone_redundant,
            frontend_type=frontend_type,
            public_ips=public_ips,
            public_ip_zones_mismatch=public_ip_zones_mismatch,
            classification=classification,
            recommendation=recommendation
        )
