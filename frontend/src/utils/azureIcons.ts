// Azure service icons served from /public/Icons (Vite static assets)
// Now using semantic icon resolver with LLM-provided category + service name

import { resolveAzureIcon } from './azureIconResolver';

export const getAzureIcon = (resourceType: string): string => {
  const iconMap: Record<string, string> = {
    // Compute
    vm: "/Icons/compute/10028-icon-service-Virtual-Machines-(Classic).svg",
    disk: "/Icons/compute/10032-icon-service-Disks.svg",
    
    // Containers
    aks: "/Icons/containers/10023-icon-service-Kubernetes-Services.svg",
    
    // Networking
    vnet: "/Icons/networking/10061-icon-service-Virtual-Networks.svg",
    subnet: "/Icons/networking/02742-icon-service-Subnet.svg",
    nsg: "/Icons/networking/10067-icon-service-Network-Security-Groups.svg",
    pip: "/Icons/networking/10068-icon-service-Public-IP-Addresses-(Classic).svg",
    nic: "/Icons/networking/10080-icon-service-Network-Interfaces.svg",
    private_endpoint: "/Icons/networking/00427-icon-service-Private-Link.svg",
    network: "/Icons/networking/10061-icon-service-Virtual-Networks.svg",
    
    // Storage
    storage: "/Icons/storage/10086-icon-service-Storage-Accounts.svg",
    
    // Databases
    sql: "/Icons/databases/10130-icon-service-SQL-Databases.svg",
    
    // Security
    keyvault: "/Icons/security/10245-icon-service-Key-Vaults.svg",
    
    //AI
    aoai: "/Icons/ai + machine learning/03438-icon-service-Azure-OpenAI.svg",

    // Management + Governance
    schd: "/Icons/general/10833-icon-service-Scheduler.svg",

    // Default / catch-all
    resource: "/Icons/general/10001-icon-service-All-Resources.svg",
  };

  return iconMap[resourceType.toLowerCase()] || "/Icons/general/10001-icon-service-All-Resources.svg";
};

/**
 * Get Azure icon using LLM semantic metadata (preferred method).
 * Uses category + service name for deterministic icon resolution.
 * 
 * @param category - Azure service category (e.g. "Compute", "Containers", "Networking")
 * @param serviceName - Azure service name (e.g. "Azure Kubernetes Service", "Virtual Machines")
 * @param resourceType - Optional Azure resource type (e.g. "microsoft.app/containerapps")
 * @returns Path to the most appropriate icon
 */
export const getAzureIconSemantic = (
  category: string,
  serviceName: string,
  resourceType?: string
): string => {
  return resolveAzureIcon(category, serviceName, resourceType);
};
