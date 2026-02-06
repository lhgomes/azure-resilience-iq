#!/usr/bin/env python3
"""Test script to verify backend pool ID extraction fix"""

import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))

from app.collector.arg import populate_backend_pool_ids

# Mock data representing the actual structure
test_resources = [
    # VM resource
    {
        'id': '/subscriptions/59e12ca5-d654-418e-bc74-ef6f56c92836/resourcegroups/rg-msl-online/providers/microsoft.compute/virtualmachines/appvm0',
        'type': 'microsoft.compute/virtualmachines',
        'properties': {
            'networkProfile': {
                'networkInterfaces': [
                    {
                        'id': '/subscriptions/59e12ca5-d654-418e-bc74-ef6f56c92836/resourcegroups/rg-msl-online/providers/microsoft.network/networkinterfaces/appvm0-nic'
                    }
                ]
            }
        },
        'backend_pool_ids': None  # Should be populated
    },
    # NIC resource with backend pool reference
    {
        'id': '/subscriptions/59e12ca5-d654-418e-bc74-ef6f56c92836/resourcegroups/rg-msl-online/providers/microsoft.network/networkinterfaces/appvm0-nic',
        'type': 'microsoft.network/networkinterfaces',
        'properties': {
            'ipConfigurations': [
                {
                    'properties': {
                        'loadBalancerBackendAddressPools': [
                            {
                                'id': '/subscriptions/59e12ca5-d654-418e-bc74-ef6f56c92836/resourcegroups/rg-msl-online/providers/microsoft.network/loadbalancers/app-tier-lb/backendaddresspools/app-lb-be'
                            }
                        ]
                    }
                }
            ]
        }
    }
]

print("Before processing:")
print(f"VM backend_pool_ids: {test_resources[0].get('backend_pool_ids')}")

# Run the population function
populate_backend_pool_ids(test_resources)

print("\nAfter processing:")
print(f"VM backend_pool_ids: {test_resources[0].get('backend_pool_ids')}")

expected = '/subscriptions/59e12ca5-d654-418e-bc74-ef6f56c92836/resourcegroups/rg-msl-online/providers/microsoft.network/loadbalancers/app-tier-lb/backendaddresspools/app-lb-be'

if test_resources[0].get('backend_pool_ids') and expected in test_resources[0]['backend_pool_ids']:
    print("\n✅ TEST PASSED: Backend pool ID correctly extracted from NIC!")
else:
    print("\n❌ TEST FAILED: Backend pool ID not found")
    print(f"Expected: {expected}")
    print(f"Got: {test_resources[0].get('backend_pool_ids')}")
