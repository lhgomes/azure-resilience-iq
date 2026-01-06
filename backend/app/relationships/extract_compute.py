"""
Extract compute resource relationships (VM → disk, VM → NIC, etc.)
"""

from typing import Any, Dict, List, Tuple
from .utils import norm_id


def extract_compute_relationships(resources_by_id: Dict[str, Dict[str, Any]]) -> List[Tuple[str, str, str, str, float, list]]:
    """
    Extract relationships from compute resources (VMs, VMScaleSets, etc.)
    
    Returns:
        List of (from_id, to_id, relationship_type, signal_type, confidence, evidence_list)
    """
    edges = []
    
    for rid, resource in resources_by_id.items():
        rtype = (resource.get('type') or '').lower()
        props = resource.get('properties') or {}
        
        # Virtual Machines
        if rtype == 'microsoft.compute/virtualmachines':
            edges.extend(_extract_vm_relationships(rid, props, resources_by_id))
        
        # VM Scale Sets
        elif rtype == 'microsoft.compute/virtualmachinescalesets':
            edges.extend(_extract_vmss_relationships(rid, props, resources_by_id))
    
    return edges


def _extract_vm_relationships(vm_id: str, props: Dict[str, Any], resources_by_id: Dict[str, Any]) -> List[Tuple[str, str, str, str, float, list]]:
    """Extract relationships from a Virtual Machine"""
    edges = []
    
    # 1. Storage Profile - OS Disk
    storage_profile = props.get('storageProfile') or {}
    os_disk = storage_profile.get('osDisk') or {}
    
    if 'managedDisk' in os_disk:
        disk_ref = os_disk['managedDisk']
        if 'id' in disk_ref:
            disk_id = norm_id(disk_ref['id'])
            if disk_id in resources_by_id:
                edges.append((
                    vm_id,
                    disk_id,
                    'uses_os_disk',
                    'ARM_Declared',
                    0.98,
                    [{'field': 'storageProfile.osDisk.managedDisk.id', 'value': disk_ref['id']}]
                ))
    
    # 2. Storage Profile - Data Disks
    data_disks = storage_profile.get('dataDisks') or []
    for idx, data_disk in enumerate(data_disks):
        if 'managedDisk' in data_disk:
            disk_ref = data_disk['managedDisk']
            if 'id' in disk_ref:
                disk_id = norm_id(disk_ref['id'])
                if disk_id in resources_by_id:
                    edges.append((
                        vm_id,
                        disk_id,
                        'uses_data_disk',
                        'ARM_Declared',
                        0.98,
                        [{'field': f'storageProfile.dataDisks[{idx}].managedDisk.id', 'value': disk_ref['id']}]
                    ))
    
    # 3. Network Profile - Network Interfaces
    network_profile = props.get('networkProfile') or {}
    network_interfaces = network_profile.get('networkInterfaces') or []
    
    for idx, nic_ref in enumerate(network_interfaces):
        if 'id' in nic_ref:
            nic_id = norm_id(nic_ref['id'])
            if nic_id in resources_by_id:
                is_primary = nic_ref.get('properties', {}).get('primary', False)
                relationship = 'uses_primary_nic' if is_primary else 'uses_nic'
                edges.append((
                    vm_id,
                    nic_id,
                    relationship,
                    'ARM_Declared',
                    0.98,
                    [{'field': f'networkProfile.networkInterfaces[{idx}].id', 'value': nic_ref['id']}]
                ))
    
    # 4. Diagnostics Profile - Storage Account (if using custom storage)
    diagnostics_profile = props.get('diagnosticsProfile') or {}
    boot_diagnostics = diagnostics_profile.get('bootDiagnostics') or {}
    
    if boot_diagnostics.get('enabled') and 'storageUri' in boot_diagnostics:
        storage_uri = boot_diagnostics['storageUri']
        # Extract storage account name from URI (e.g., https://mystorageaccount.blob.core.windows.net/)
        if storage_uri:
            storage_account_name = storage_uri.split('//')[1].split('.')[0] if '//' in storage_uri else None
            if storage_account_name:
                # Try to find the storage account resource
                for resource_id, resource in resources_by_id.items():
                    if resource.get('type', '').lower() == 'microsoft.storage/storageaccounts':
                        if resource.get('name', '').lower() == storage_account_name.lower():
                            edges.append((
                                vm_id,
                                resource_id,
                                'uses_boot_diagnostics',
                                'ARM_Declared',
                                0.95,
                                [{'field': 'diagnosticsProfile.bootDiagnostics.storageUri', 'value': storage_uri}]
                            ))
                            break
    
    # 5. SSH Public Keys (match by key data)
    os_profile = props.get('osProfile') or {}
    linux_config = os_profile.get('linuxConfiguration') or {}
    ssh_config = linux_config.get('ssh') or {}
    public_keys = ssh_config.get('publicKeys') or []
    
    for pk in public_keys:
        key_data = pk.get('keyData', '').strip()
        if key_data:
            # Try to find matching SSH public key resource
            for resource_id, resource in resources_by_id.items():
                if resource.get('type', '').lower() == 'microsoft.compute/sshpublickeys':
                    resource_key_data = resource.get('properties', {}).get('publicKey', '').strip()
                    if resource_key_data == key_data:
                        edges.append((
                            vm_id,
                            resource_id,
                            'uses_ssh_key',
                            'ARM_Declared',
                            0.95,  # Slightly lower confidence since it's a content match, not ID reference
                            [{'field': 'osProfile.linuxConfiguration.ssh.publicKeys[].keyData', 'match': 'content'}]
                        ))
                        break
    
    return edges


def _extract_vmss_relationships(vmss_id: str, props: Dict[str, Any], resources_by_id: Dict[str, Any]) -> List[Tuple[str, str, str, str, float, list]]:
    """Extract relationships from a VM Scale Set"""
    edges = []
    
    # VMSS virtual machine profile
    vm_profile = props.get('virtualMachineProfile') or {}
    
    # 1. Storage Profile
    storage_profile = vm_profile.get('storageProfile') or {}
    os_disk = storage_profile.get('osDisk') or {}
    
    # Image reference (if using custom image)
    image_ref = storage_profile.get('imageReference') or {}
    if 'id' in image_ref:
        image_id = norm_id(image_ref['id'])
        if image_id in resources_by_id:
            edges.append((
                vmss_id,
                image_id,
                'uses_image',
                'ARM_Declared',
                0.98,
                [{'field': 'virtualMachineProfile.storageProfile.imageReference.id', 'value': image_ref['id']}]
            ))
    
    # 2. Network Profile
    network_profile = vm_profile.get('networkProfile') or {}
    nic_configs = network_profile.get('networkInterfaceConfigurations') or []
    
    for idx, nic_config in enumerate(nic_configs):
        nic_props = nic_config.get('properties') or {}
        ip_configs = nic_props.get('ipConfigurations') or []
        
        # Extract subnet references
        for ip_idx, ip_config in enumerate(ip_configs):
            ip_props = ip_config.get('properties') or {}
            if 'subnet' in ip_props and 'id' in ip_props['subnet']:
                subnet_id = norm_id(ip_props['subnet']['id'])
                if subnet_id in resources_by_id:
                    edges.append((
                        vmss_id,
                        subnet_id,
                        'uses_subnet',
                        'ARM_Declared',
                        0.98,
                        [{'field': f'virtualMachineProfile.networkProfile.networkInterfaceConfigurations[{idx}].ipConfigurations[{ip_idx}].subnet.id', 'value': ip_props['subnet']['id']}]
                    ))
            
            # Application Gateway backend address pools
            if 'applicationGatewayBackendAddressPools' in ip_props:
                for pool_idx, pool_ref in enumerate(ip_props['applicationGatewayBackendAddressPools']):
                    if 'id' in pool_ref:
                        pool_id = norm_id(pool_ref['id'])
                        edges.append((
                            vmss_id,
                            pool_id,
                            'backend_pool_member',
                            'ARM_Declared',
                            0.98,
                            [{'field': f'virtualMachineProfile.networkProfile...applicationGatewayBackendAddressPools[{pool_idx}].id', 'value': pool_ref['id']}]
                        ))
            
            # Load Balancer backend address pools
            if 'loadBalancerBackendAddressPools' in ip_props:
                for pool_idx, pool_ref in enumerate(ip_props['loadBalancerBackendAddressPools']):
                    if 'id' in pool_ref:
                        pool_id = norm_id(pool_ref['id'])
                        edges.append((
                            vmss_id,
                            pool_id,
                            'backend_pool_member',
                            'ARM_Declared',
                            0.98,
                            [{'field': f'virtualMachineProfile.networkProfile...loadBalancerBackendAddressPools[{pool_idx}].id', 'value': pool_ref['id']}]
                        ))
        
        # Network Security Group
        if 'networkSecurityGroup' in nic_props and 'id' in nic_props['networkSecurityGroup']:
            nsg_id = norm_id(nic_props['networkSecurityGroup']['id'])
            if nsg_id in resources_by_id:
                edges.append((
                    vmss_id,
                    nsg_id,
                    'protected_by_nsg',
                    'ARM_Declared',
                    0.98,
                    [{'field': f'virtualMachineProfile.networkProfile.networkInterfaceConfigurations[{idx}].networkSecurityGroup.id', 'value': nic_props['networkSecurityGroup']['id']}]
                ))
    
    return edges
