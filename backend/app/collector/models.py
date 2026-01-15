from pydantic import BaseModel
from typing import Dict, Any, Optional, List


class AzureResource(BaseModel):
    id: str
    name: str
    type: str
    location: Optional[str]
    resource_group: str
    subscription_id: str
    tags: Dict[str, Any] = {}
    properties: Dict[str, Any] = {}
    sku: Optional[Dict[str, Any]] = None  # SKU information for zone redundancy detection
    zones: Optional[List[str]] = None  # Availability zones for zone-aware resources
    
    # Parent/Related Resource Relationships for Resilience Grouping
    parent_resource_id: Optional[str] = None  # e.g., VM → AvailabilitySet
    parent_resource_type: Optional[str] = None  # Type of parent resource
    child_resource_ids: Optional[List[str]] = None  # e.g., AvailabilitySet → [VMs]
    backend_pool_ids: Optional[List[str]] = None  # Load Balancer backend pool memberships
    failover_group_id: Optional[str] = None  # Failover group membership (SQL, Cosmos)
    replica_regions: Optional[Dict[str, str]] = None  # Multi-region: {region: resource_id}
