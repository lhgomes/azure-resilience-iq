"""
Unit tests for zonal_analyzer module.

Tests zone detection logic, deployment pattern classification,
and summary statistics calculation.
"""

import pytest
from app.resilience.zonal_analyzer import (
    ZonalAnalyzer,
    ZonalResilienceSummary,
    DeploymentPattern,
    ZonalData,
)


class TestZonalAnalyzer:
    """Test suite for ZonalAnalyzer class."""
    
    def test_zone_redundant_storage_zrs(self):
        """Test detection of Zone-Redundant Storage (ZRS)."""
        resource = {
            "id": "/subscriptions/xxx/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/myaccount",
            "name": "myaccount",
            "type": "Microsoft.Storage/storageAccounts",
            "sku": {"name": "Standard_ZRS"},
            "properties": {}
        }
        
        result = ZonalAnalyzer.extract_zonal_data(resource)
        
        assert result.is_zone_redundant is True
        assert result.deployment_pattern == DeploymentPattern.ZONE_REDUNDANT
        assert result.meets_3az_requirement is True
        assert "Zone-redundant" in result.recommendation
    
    def test_zone_redundant_storage_gzrs(self):
        """Test detection of Geo-Zone-Redundant Storage (GZRS)."""
        resource = {
            "id": "/subscriptions/xxx/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/myaccount",
            "name": "myaccount",
            "type": "Microsoft.Storage/storageAccounts",
            "sku": {"name": "Standard_GZRS"},
            "properties": {}
        }
        
        result = ZonalAnalyzer.extract_zonal_data(resource)
        
        assert result.is_zone_redundant is True
        assert result.deployment_pattern == DeploymentPattern.ZONE_REDUNDANT
        assert result.meets_3az_requirement is True
    
    def test_multi_zone_3az_deployment(self):
        """Test detection of 3-AZ multi-zone deployment."""
        resource = {
            "id": "/subscriptions/xxx/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm1",
            "name": "vm1",
            "type": "Microsoft.Compute/virtualMachines",
            "properties": {
                "zones": ["1", "2", "3"]
            }
        }
        
        result = ZonalAnalyzer.extract_zonal_data(resource)
        
        assert result.is_zone_redundant is False
        assert result.deployment_pattern == DeploymentPattern.MULTI_ZONE
        assert result.zone_count == 3
        assert result.zones_used == ["1", "2", "3"]
        assert result.meets_3az_requirement is True
        assert "3 zones" in result.recommendation
    
    def test_multi_zone_2az_deployment(self):
        """Test detection of 2-AZ multi-zone deployment."""
        resource = {
            "id": "/subscriptions/xxx/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm1",
            "name": "vm1",
            "type": "Microsoft.Compute/virtualMachines",
            "properties": {
                "zones": ["1", "2"]
            }
        }
        
        result = ZonalAnalyzer.extract_zonal_data(resource)
        
        assert result.deployment_pattern == DeploymentPattern.MULTI_ZONE
        assert result.zone_count == 2
        assert result.zones_used == ["1", "2"]
        assert result.meets_3az_requirement is False
        assert "2 zones" in result.recommendation
    
    def test_single_zone_deployment(self):
        """Test detection of single-zone deployment."""
        resource = {
            "id": "/subscriptions/xxx/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm1",
            "name": "vm1",
            "type": "Microsoft.Compute/virtualMachines",
            "properties": {
                "zones": ["1"]
            }
        }
        
        result = ZonalAnalyzer.extract_zonal_data(resource)
        
        assert result.deployment_pattern == DeploymentPattern.SINGLE_ZONE
        assert result.zone_count == 1
        assert result.zones_used == ["1"]
        assert result.meets_3az_requirement is False
        assert "Single-zone" in result.recommendation
    
    def test_zones_at_top_level(self):
        """Test detection when zones property is at resource top level."""
        resource = {
            "id": "/subscriptions/xxx/resourceGroups/rg/providers/Microsoft.Compute/disks/disk1",
            "name": "disk1",
            "type": "Microsoft.Compute/disks",
            "zones": ["1", "2", "3"],
            "properties": {}
        }
        
        result = ZonalAnalyzer.extract_zonal_data(resource)
        
        assert result.zone_count == 3
        assert result.zones_used == ["1", "2", "3"]
        assert result.meets_3az_requirement is True
    
    def test_unknown_zone_configuration(self):
        """Test handling of resources without zone information."""
        resource = {
            "id": "/subscriptions/xxx/resourceGroups/rg/providers/Microsoft.Web/sites/webapp",
            "name": "webapp",
            "type": "Microsoft.Web/sites",
            "properties": {}
        }
        
        result = ZonalAnalyzer.extract_zonal_data(resource)
        
        assert result.deployment_pattern == DeploymentPattern.UNKNOWN
        assert result.zone_count == 0
        assert result.zones_used == []
        assert result.meets_3az_requirement is False
        assert "unknown" in result.recommendation.lower()
    
    def test_zone_redundant_property(self):
        """Test detection of zoneRedundant property."""
        resource = {
            "id": "/subscriptions/xxx/resourceGroups/rg/providers/Microsoft.ServiceBus/namespaces/sb",
            "name": "sb",
            "type": "Microsoft.ServiceBus/namespaces",
            "sku": {"name": "Premium"},
            "properties": {
                "zoneRedundant": True
            }
        }
        
        result = ZonalAnalyzer.extract_zonal_data(resource)
        
        assert result.is_zone_redundant is True
        assert result.deployment_pattern == DeploymentPattern.ZONE_REDUNDANT
        assert result.meets_3az_requirement is True
    
    def test_zones_as_string(self):
        """Test handling of zones as a single string value."""
        resource = {
            "id": "/subscriptions/xxx/resourceGroups/rg/providers/Microsoft.Compute/disks/disk1",
            "name": "disk1",
            "type": "Microsoft.Compute/disks",
            "properties": {
                "zones": "1"
            }
        }
        
        result = ZonalAnalyzer.extract_zonal_data(resource)
        
        assert result.zones_used == ["1"]
        assert result.zone_count == 1
        assert result.deployment_pattern == DeploymentPattern.SINGLE_ZONE


class TestZonalResilienceSummary:
    """Test suite for ZonalResilienceSummary class."""
    
    def test_empty_resources(self):
        """Test summary with no resources."""
        summary = ZonalResilienceSummary([])
        
        assert summary.total_resources == 0
        assert summary.zone_redundant_count == 0
        assert summary.multi_zone_count == 0
        assert summary.single_zone_count == 0
        assert summary.unknown_count == 0
        assert summary.is_3az_compliant is False
        assert summary.zonal_resilience_score == 0.0
    
    def test_all_zone_redundant(self):
        """Test summary with all zone-redundant resources."""
        zonal_data = [
            ZonalData(
                zones_used=[],
                is_zone_redundant=True,
                zone_count=0,
                meets_3az_requirement=True,
                deployment_pattern=DeploymentPattern.ZONE_REDUNDANT,
                recommendation="Zone-redundant"
            )
            for _ in range(5)
        ]
        
        summary = ZonalResilienceSummary(zonal_data)
        
        assert summary.total_resources == 5
        assert summary.zone_redundant_count == 5
        assert summary.compliant_3az_count == 5
        assert summary.is_3az_compliant is True
        assert summary.zonal_resilience_score == 1.0
    
    def test_mixed_deployment_patterns(self):
        """Test summary with mixed deployment patterns."""
        zonal_data = [
            # 2 zone-redundant
            ZonalData([], True, 0, True, DeploymentPattern.ZONE_REDUNDANT, ""),
            ZonalData([], True, 0, True, DeploymentPattern.ZONE_REDUNDANT, ""),
            # 2 multi-zone (3-AZ)
            ZonalData(["1","2","3"], False, 3, True, DeploymentPattern.MULTI_ZONE, ""),
            ZonalData(["1","2","3"], False, 3, True, DeploymentPattern.MULTI_ZONE, ""),
            # 1 single-zone
            ZonalData(["1"], False, 1, False, DeploymentPattern.SINGLE_ZONE, ""),
            # 1 unknown
            ZonalData([], False, 0, False, DeploymentPattern.UNKNOWN, ""),
        ]
        
        summary = ZonalResilienceSummary(zonal_data)
        
        assert summary.total_resources == 6
        assert summary.zone_redundant_count == 2
        assert summary.multi_zone_count == 2
        assert summary.single_zone_count == 1
        assert summary.unknown_count == 1
        assert summary.compliant_3az_count == 4  # 2 zone-redundant + 2 multi-zone
        assert summary.is_3az_compliant is False  # Not ALL resources compliant
        
        # Score = (2 * 1.0 + 2 * 0.8 + 1 * 0.3 + 1 * 0.0) / 6
        expected_score = (2.0 + 1.6 + 0.3) / 6
        assert abs(summary.zonal_resilience_score - expected_score) < 0.01
    
    def test_to_dict(self):
        """Test dictionary serialization."""
        zonal_data = [
            ZonalData(["1","2","3"], False, 3, True, DeploymentPattern.MULTI_ZONE, ""),
            ZonalData([], True, 0, True, DeploymentPattern.ZONE_REDUNDANT, ""),
        ]
        
        summary = ZonalResilienceSummary(zonal_data)
        result = summary.to_dict()
        
        assert isinstance(result, dict)
        assert result["total_resources"] == 2
        assert result["zone_redundant_resources"] == 1
        assert result["multi_zone_resources"] == 1
        assert result["compliant_3az_resources"] == 2
        assert result["overall_3az_compliant"] is True
        assert "zonal_resilience_score" in result
        assert "compliance_percentage" in result
        assert result["compliance_percentage"] == 100.0
    
    def test_compliance_percentage(self):
        """Test compliance percentage calculation."""
        zonal_data = [
            ZonalData(["1","2","3"], False, 3, True, DeploymentPattern.MULTI_ZONE, ""),
            ZonalData(["1","2","3"], False, 3, True, DeploymentPattern.MULTI_ZONE, ""),
            ZonalData(["1"], False, 1, False, DeploymentPattern.SINGLE_ZONE, ""),
            ZonalData([], False, 0, False, DeploymentPattern.UNKNOWN, ""),
        ]
        
        summary = ZonalResilienceSummary(zonal_data)
        result = summary.to_dict()
        
        # 2 out of 4 compliant = 50%
        assert result["compliance_percentage"] == 50.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
