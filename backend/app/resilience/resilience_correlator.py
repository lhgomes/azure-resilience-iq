"""
Resiliency Group Identification and Management

This module identifies and manages resilience groups - collections of Azure resources
that work together to provide resilience (e.g., VMs in an Availability Set, resources
behind a Load Balancer, database replicas, etc.).

Resiliency groups enable:
1. Context-aware zone analysis (analyzing group resilience, not individual resources)
2. Automatic graph grouping for visualization
3. Accurate resilience scoring and recommendations
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Any, Optional, Set
import logging

from app.relationships.utils import norm_id, short_id

logger = logging.getLogger(__name__)


class ResiliencyGroupType(Enum):
    """Types of resilience groups"""
    AVAILABILITY_SET = "availability_set"
    VMSS = "vmss"
    LOAD_BALANCER_BACKEND = "load_balancer_backend"
    APPLICATION_GATEWAY_BACKEND = "application_gateway_backend"
    STORAGE_REDUNDANCY = "storage_redundancy"
    DATABASE_FAILOVER = "database_failover"
    REPLICATED_RESOURCE = "replicated_resource"
    COSMOS_REPLICATED = "cosmos_replicated"


@dataclass
class ResiliencyGroup:
    """Represents a group of related resilience resources"""
    id: str  # Unique group ID (usually parent resource ID)
    name: str  # Display name
    type: ResiliencyGroupType
    member_ids: List[str]  # Resource IDs in this group
    member_types: List[str]  # Resource types of members
    metadata: Dict[str, Any]  # Group-specific metadata
    
    def __hash__(self):
        return hash(self.id)
    
    def __eq__(self, other):
        if not isinstance(other, ResiliencyGroup):
            return False
        return self.id == other.id


class ResourceCorrelator:
    """
    Identifies and manages resilience groups from a collection of Azure resources.
    
    Builds relationships between resources to understand:
    - Which VMs belong to which Availability Sets
    - Which resources are behind which Load Balancers
    - Which databases are replicated together
    - Which storage accounts have geo-redundancy
    """
    
    def __init__(self, all_resources: List[Dict[str, Any]]):
        """
        Initialize correlator with all resources.
        
        Args:
            all_resources: List of Azure resources from collector
        """
        self.all_resources = all_resources
        self.resource_map: Dict[str, Dict[str, Any]] = {
            r.get('id'): r for r in all_resources if r.get('id')
        }
        self.groups: List[ResiliencyGroup] = []
    
    def identify_groups(self) -> List[ResiliencyGroup]:
        """
        Identify all resilience groups in the resource collection.
        
        Returns:
            List of identified resilience groups
        """
        logger.info(f"Identifying resilience groups from {len(self.all_resources)} resources...")
        
        self._identify_availability_set_groups()
        self._identify_vmss_groups()
        self._identify_load_balancer_backend_groups()
        self._identify_application_gateway_backend_groups()
        self._identify_storage_redundancy_groups()
        self._identify_database_failover_groups()
        self._identify_cosmos_replication_groups()
        
        logger.info(f"Identified {len(self.groups)} resilience groups")
        return self.groups
    
    def _identify_availability_set_groups(self) -> None:
        """Find VMs in Availability Sets and group them"""
        as_members: Dict[str, List[Dict[str, Any]]] = {}
        as_info: Dict[str, Dict[str, Any]] = {}
        
        # Map VMs to their parent AS
        for resource in self.all_resources:
            parent_id = resource.get('parent_resource_id')
            if not parent_id:
                continue
            
            parent_type = resource.get('parent_resource_type', '').lower()
            if 'availabilityset' not in parent_type:
                continue
            
            if parent_id not in as_members:
                as_members[parent_id] = []
                # Store parent AS info
                parent = self.resource_map.get(parent_id)
                if parent:
                    as_info[parent_id] = parent
            
            as_members[parent_id].append(resource)
        
        # Create groups for each AS with multiple members
        for as_id, members in as_members.items():
            if len(members) < 1:  # Even single VM in AS is a group
                continue
            
            as_resource = as_info.get(as_id) or self.resource_map.get(as_id)
            as_name = as_resource.get('name', as_id) if as_resource else as_id
            
            group = ResiliencyGroup(
                id=as_id,
                name=f"Availability Set: {as_name}",
                type=ResiliencyGroupType.AVAILABILITY_SET,
                member_ids=[m.get('id') for m in members if m.get('id')],
                member_types=[m.get('type', 'Unknown') for m in members],
                metadata={
                    'parent_name': as_name,
                    'parent_type': 'Microsoft.Compute/availabilitySets',
                    'member_count': len(members),
                    'member_types': list(set(m.get('type', 'Unknown') for m in members))
                }
            )
            self.groups.append(group)
            logger.debug(f"Found AS group: {as_name} with {len(members)} members")
    
    def _identify_vmss_groups(self) -> None:
        """Find VM instances in VMSS and group them"""
        vmss_members: Dict[str, List[Dict[str, Any]]] = {}
        vmss_info: Dict[str, Dict[str, Any]] = {}
        
        # Map VM instances to their parent VMSS
        for resource in self.all_resources:
            parent_id = resource.get('parent_resource_id')
            if not parent_id:
                continue
            
            parent_type = resource.get('parent_resource_type', '').lower()
            if 'vmss' not in parent_type and 'scaleset' not in parent_type:
                continue
            
            if parent_id not in vmss_members:
                vmss_members[parent_id] = []
                parent = self.resource_map.get(parent_id)
                if parent:
                    vmss_info[parent_id] = parent
            
            vmss_members[parent_id].append(resource)
        
        # Create groups for each VMSS
        for vmss_id, members in vmss_members.items():
            if not members:
                continue
            
            vmss_resource = vmss_info.get(vmss_id) or self.resource_map.get(vmss_id)
            vmss_name = vmss_resource.get('name', vmss_id) if vmss_resource else vmss_id
            
            group = ResiliencyGroup(
                id=vmss_id,
                name=f"VM Scale Set: {vmss_name}",
                type=ResiliencyGroupType.VMSS,
                member_ids=[m.get('id') for m in members if m.get('id')],
                member_types=[m.get('type', 'Unknown') for m in members],
                metadata={
                    'parent_name': vmss_name,
                    'parent_type': 'Microsoft.Compute/virtualMachineScaleSets',
                    'instance_count': len(members),
                    'zones': vmss_resource.get('zones', []) if vmss_resource else []
                }
            )
            self.groups.append(group)
            logger.debug(f"Found VMSS group: {vmss_name} with {len(members)} instances")
    
    def _identify_load_balancer_backend_groups(self) -> None:
        """Find resources behind Load Balancers and group them by backend pool."""
        # Build pool membership map from resources
        pool_members: Dict[str, List[Dict[str, Any]]] = {}
        for resource in self.all_resources:
            backend_pool_ids = resource.get('backend_pool_ids', [])
            if not backend_pool_ids or not isinstance(backend_pool_ids, list):
                continue

            rtype = resource.get('type', '').lower()
            if 'virtualmachine' not in rtype and 'networkinterface' not in rtype:
                continue

            for pool_id in backend_pool_ids:
                if not isinstance(pool_id, str):
                    continue
                pool_id_norm = norm_id(pool_id)
                if not pool_id_norm:
                    continue
                pool_members.setdefault(pool_id_norm, []).append(resource)

        # Find all Load Balancers
        load_balancers = [
            r for r in self.all_resources
            if 'loadbalancer' in r.get('type', '').lower()
        ]

        for lb in load_balancers:
            lb_id = lb.get('id')
            if not lb_id:
                continue
            lb_id_norm = norm_id(lb_id)
            lb_name = lb.get('name')

            pools = lb.get('properties', {}).get('backendAddressPools', [])
            if not isinstance(pools, list):
                continue

            for pool in pools:
                if not isinstance(pool, dict):
                    continue

                pool_id = pool.get('id')
                if not isinstance(pool_id, str):
                    continue
                pool_id_norm = norm_id(pool_id)
                pool_name = pool.get('name')

                backend_members = pool_members.get(pool_id_norm, [])
                if not backend_members:
                    continue

                group = ResiliencyGroup(
                    id=short_id(pool_id_norm),
                    name=f"Load Balancer Backend Pool: {lb_name}/{pool_name}" if pool_name else f"Load Balancer Backend: {lb_name}",
                    type=ResiliencyGroupType.LOAD_BALANCER_BACKEND,
                    member_ids=[m.get('id') for m in backend_members if m.get('id')],
                    member_types=[m.get('type', 'Unknown') for m in backend_members],
                    metadata={
                        'load_balancer_id': lb_id_norm,
                        'load_balancer_name': lb_name,
                        'backend_pool_id': pool_id_norm,
                        'backend_pool_name': pool_name,
                        'is_zone_redundant': lb.get('is_zone_redundant', False),
                        'member_count': len(backend_members),
                    }
                )
                self.groups.append(group)
                logger.debug(
                    f"Found LB backend pool group: {lb_name}/{pool_name} with {len(backend_members)} members"
                )

    def _identify_application_gateway_backend_groups(self) -> None:
        """Find resources behind Application Gateways and group them"""
        app_gateways = [
            r for r in self.all_resources
            if 'microsoft.network/applicationgateways' in r.get('type', '').lower()
        ]

        for agw in app_gateways:
            agw_id = agw.get('id')
            agw_name = agw.get('name')
            agw_zones = agw.get('zones', [])
            is_zone_redundant = isinstance(agw_zones, list) and len(agw_zones) >= 2

            backend_members = []
            for resource in self.all_resources:
                backend_pool_ids = resource.get('backend_pool_ids', [])
                if backend_pool_ids and isinstance(backend_pool_ids, list):
                    for pool_id in backend_pool_ids:
                        if isinstance(pool_id, str) and agw_id and agw_id.lower() in pool_id.lower():
                            rtype = resource.get('type', '').lower()
                            if 'virtualmachine' in rtype or 'networkinterface' in rtype:
                                if resource not in backend_members:
                                    backend_members.append(resource)
                            break
                elif resource.get('parent_resource_id') == agw_id:
                    backend_members.append(resource)

            if backend_members:
                group = ResiliencyGroup(
                    id=f"{agw_id}:backend",
                    name=f"Application Gateway Backend: {agw_name}",
                    type=ResiliencyGroupType.APPLICATION_GATEWAY_BACKEND,
                    member_ids=[m.get('id') for m in backend_members if m.get('id')],
                    member_types=[m.get('type', 'Unknown') for m in backend_members],
                    metadata={
                        'application_gateway_id': agw_id,
                        'application_gateway_name': agw_name,
                        'is_zone_redundant': is_zone_redundant,
                        'member_count': len(backend_members),
                        'zones': agw_zones,
                    }
                )
                self.groups.append(group)
                logger.debug(
                    f"Found Application Gateway backend group: {agw_name} with {len(backend_members)} members"
                )
    
    def _identify_storage_redundancy_groups(self) -> None:
        """Find storage accounts with geo-redundancy configuration"""
        storage_accounts = [
            r for r in self.all_resources
            if 'storageaccount' in r.get('type', '').lower()
        ]
        
        for storage in storage_accounts:
            properties = storage.get('properties', {})
            sku = properties.get('accessTier')
            
            # Check for replication/redundancy patterns
            replica_regions = storage.get('replica_regions')
            if replica_regions and isinstance(replica_regions, dict):
                # Multi-region replication detected
                group = ResiliencyGroup(
                    id=f"{storage.get('id')}:replication",
                    name=f"Storage Geo-Replication: {storage.get('name')}",
                    type=ResiliencyGroupType.REPLICATED_RESOURCE,
                    member_ids=[storage.get('id')] + list(replica_regions.values()),
                    member_types=['Storage'] * (1 + len(replica_regions)),
                    metadata={
                        'primary_region': storage.get('location'),
                        'replica_regions': list(replica_regions.keys()),
                        'redundancy_type': 'geo-redundant'
                    }
                )
                self.groups.append(group)
                logger.debug(f"Found storage geo-replication: {storage.get('name')} in {len(replica_regions)} regions")
    
    def _identify_database_failover_groups(self) -> None:
        """Find SQL databases in failover groups"""
        sql_servers = [
            r for r in self.all_resources
            if 'sql/servers/failovergroups' in r.get('type', '').lower()
        ]
        
        for failover_group in sql_servers:
            failover_id = failover_group.get('id')
            failover_name = failover_group.get('name')
            
            # Find databases that belong to this failover group
            member_databases = [
                r for r in self.all_resources
                if r.get('failover_group_id') == failover_id
            ]
            
            if member_databases:
                group = ResiliencyGroup(
                    id=failover_id,
                    name=f"SQL Failover Group: {failover_name}",
                    type=ResiliencyGroupType.DATABASE_FAILOVER,
                    member_ids=[m.get('id') for m in member_databases if m.get('id')],
                    member_types=[m.get('type', 'Unknown') for m in member_databases],
                    metadata={
                        'failover_group_name': failover_name,
                        'database_count': len(member_databases),
                        'replication_role': failover_group.get('properties', {}).get('replicationRole')
                    }
                )
                self.groups.append(group)
                logger.debug(f"Found SQL failover group: {failover_name} with {len(member_databases)} databases")
    
    def _identify_cosmos_replication_groups(self) -> None:
        """Find Cosmos DB accounts with multi-region replication"""
        cosmos_accounts = [
            r for r in self.all_resources
            if 'cosmosdb' in r.get('type', '').lower()
        ]
        
        for cosmos in cosmos_accounts:
            properties = cosmos.get('properties', {})
            locations = properties.get('locations', [])
            
            if len(locations) > 1:
                # Multi-region Cosmos DB
                group = ResiliencyGroup(
                    id=f"{cosmos.get('id')}:replication",
                    name=f"Cosmos DB Multi-Region: {cosmos.get('name')}",
                    type=ResiliencyGroupType.COSMOS_REPLICATED,
                    member_ids=[cosmos.get('id')],  # Cosmos itself represents the group
                    member_types=['Microsoft.DocumentDB/databaseAccounts'],
                    metadata={
                        'write_regions': [l.get('locationName') for l in locations if l.get('isZoneRedundant')],
                        'read_regions': [l.get('locationName') for l in locations],
                        'region_count': len(locations),
                        'zone_redundant': any(l.get('isZoneRedundant') for l in locations)
                    }
                )
                self.groups.append(group)
                logger.debug(f"Found Cosmos multi-region: {cosmos.get('name')} with {len(locations)} regions")
    
    def get_group_for_resource(self, resource_id: str) -> Optional[ResiliencyGroup]:
        """Get the resilience group a resource belongs to"""
        for group in self.groups:
            if resource_id in group.member_ids:
                return group
        return None
    
    def get_group_members(self, group_id: str) -> List[Dict[str, Any]]:
        """Get all resources in a specific group"""
        group = next((g for g in self.groups if g.id == group_id), None)
        if not group:
            return []
        
        return [
            self.resource_map[rid]
            for rid in group.member_ids
            if rid in self.resource_map
        ]
    
    def get_groups_by_type(self, group_type: ResiliencyGroupType) -> List[ResiliencyGroup]:
        """Get all groups of a specific type"""
        return [g for g in self.groups if g.type == group_type]
