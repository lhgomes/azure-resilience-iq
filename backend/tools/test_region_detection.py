#!/usr/bin/env python3
"""
Quick test to verify region-aware zone detection.
Tests that resources in non-zone regions are properly marked as NOT_APPLICABLE.
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))

from app.resilience.zonal_analyzer import ZonalAnalyzer, DeploymentPattern

# Test resource in a zone-enabled region (East US)
vm_in_zone_region = {
    "id": "/subscriptions/test/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm1",
    "name": "vm1",
    "type": "Microsoft.Compute/virtualMachines",
    "location": "eastus",  # Zone-enabled region
    "properties": {},
}

# Test resource in a non-zone region (Australia Southeast)
vm_in_non_zone_region = {
    "id": "/subscriptions/test/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm2",
    "name": "vm2",
    "type": "Microsoft.Compute/virtualMachines",
    "location": "australiasoutheast",  # NO zones support
    "properties": {},
}

# Test resource in another non-zone region (Korea South)
vm_in_korea_south = {
    "id": "/subscriptions/test/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm3",
    "name": "vm3",
    "type": "Microsoft.Compute/virtualMachines",
    "location": "koreasouth",  # NO zones support
    "properties": {},
}

# Test non-zonal resource type in zone-enabled region
ssh_key_in_zone_region = {
    "id": "/subscriptions/test/resourceGroups/rg/providers/Microsoft.Compute/sshPublicKeys/key1",
    "name": "key1",
    "type": "Microsoft.Compute/sshPublicKeys",
    "location": "eastus",  # Zone-enabled, but resource type doesn't support zones
    "properties": {},
}

print("=" * 80)
print("REGION-AWARE ZONE DETECTION TEST")
print("=" * 80)
print()

# Test 1: VM in zone-enabled region
print("Test 1: VM in East US (zone-enabled region)")
print("-" * 80)
result1 = ZonalAnalyzer.extract_zonal_data(vm_in_zone_region)
print(f"Pattern: {result1.deployment_pattern.value}")
print(f"Recommendation: {result1.recommendation}")
assert result1.deployment_pattern == DeploymentPattern.UNKNOWN, "Should be UNKNOWN (no zones configured)"
print("✓ PASS: VM in zone region without zones = UNKNOWN")
print()

# Test 2: VM in non-zone region
print("Test 2: VM in Australia Southeast (NON-zone region)")
print("-" * 80)
result2 = ZonalAnalyzer.extract_zonal_data(vm_in_non_zone_region)
print(f"Pattern: {result2.deployment_pattern.value}")
print(f"Recommendation: {result2.recommendation}")
assert result2.deployment_pattern == DeploymentPattern.NOT_APPLICABLE, "Should be NOT_APPLICABLE (region doesn't support zones)"
assert result2.meets_3az_requirement == True, "N/A resources should be compliant"
print("✓ PASS: VM in non-zone region = NOT_APPLICABLE")
print()

# Test 3: VM in Korea South (non-zone)
print("Test 3: VM in Korea South (NON-zone region)")
print("-" * 80)
result3 = ZonalAnalyzer.extract_zonal_data(vm_in_korea_south)
print(f"Pattern: {result3.deployment_pattern.value}")
print(f"Recommendation: {result3.recommendation}")
assert result3.deployment_pattern == DeploymentPattern.NOT_APPLICABLE, "Should be NOT_APPLICABLE"
assert "koreasouth" in result3.recommendation.lower(), "Should mention the region"
print("✓ PASS: VM in Korea South = NOT_APPLICABLE")
print()

# Test 4: Non-zonal resource type in zone region
print("Test 4: SSH Key in East US (zone region, but resource type doesn't support zones)")
print("-" * 80)
result4 = ZonalAnalyzer.extract_zonal_data(ssh_key_in_zone_region)
print(f"Pattern: {result4.deployment_pattern.value}")
print(f"Recommendation: {result4.recommendation}")
assert result4.deployment_pattern == DeploymentPattern.NOT_APPLICABLE, "Should be NOT_APPLICABLE (resource type)"
assert "type does not support" in result4.recommendation.lower(), "Should mention resource type"
print("✓ PASS: Non-zonal resource type = NOT_APPLICABLE")
print()

# Test region checking
print("Test 5: Region capability checking")
print("-" * 80)
print(f"East US zone-capable: {ZonalAnalyzer.is_region_zone_capable('eastus')}")
print(f"Australia Southeast zone-capable: {ZonalAnalyzer.is_region_zone_capable('australiasoutheast')}")
print(f"Korea South zone-capable: {ZonalAnalyzer.is_region_zone_capable('koreasouth')}")
print(f"West India zone-capable: {ZonalAnalyzer.is_region_zone_capable('westindia')}")
assert ZonalAnalyzer.is_region_zone_capable('eastus') == True
assert ZonalAnalyzer.is_region_zone_capable('australiasoutheast') == False
assert ZonalAnalyzer.is_region_zone_capable('koreasouth') == False
print("✓ PASS: Region capability detection working")
print()

print("=" * 80)
print("✓ ALL TESTS PASSED!")
print("=" * 80)
print()
print("Key Behaviors:")
print("1. Resources in non-zone regions → NOT_APPLICABLE (marked as compliant)")
print("2. Resources in zone regions without zones → UNKNOWN (not compliant)")
print("3. Non-zonal resource types → NOT_APPLICABLE (regardless of region)")
print("4. Region check happens BEFORE resource type check")
