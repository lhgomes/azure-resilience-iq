variable "project_name" {
  type        = string
  default     = "resilienceiq"
  description = "Project prefix used in resource naming."
}

variable "environment" {
  type        = string
  default     = "dev"
  description = "Environment label."
}

variable "location" {
  type        = string
  default     = "eastus"
  description = "Azure region."
}

variable "resource_group_name" {
  type        = string
  default     = ""
  description = "Optional custom RG name."
}

variable "vm_size" {
  type        = string
  default     = "Standard_D4s_v5"
  description = "Windows VM size."
}

variable "vm_zone" {
  type        = string
  default     = ""
  description = "Optional availability zone for the VM (e.g., 1, 2, or 3)."
}

variable "enable_bastion" {
  type        = bool
  default     = true
  description = "Whether to deploy Azure Bastion and permit Bastion-originated RDP/SSH traffic to the private VM. Enabled by default."
}

variable "admin_username" {
  type        = string
  default     = "azureadmin"
  description = "Local Windows administrator username used for bootstrap and emergency recovery."
}

variable "entra_admin_object_id" {
  type        = string
  default     = ""
  description = "Optional Entra object id to grant VM Administrator Login. Defaults to current az login principal."
}

variable "entra_login_object_ids" {
  type        = list(string)
  default     = []
  description = "Additional Entra object ids (users or groups) to grant VM Administrator Login so they can sign in with AD Integrated credentials. Required when the deploy principal differs from the interactive sign-in user (for example CI/service-principal deployments)."
}

variable "assign_entra_login_roles" {
  type        = bool
  default     = false
  description = "Whether Terraform assigns 'Virtual Machine Administrator Login' to the principals in entra_admin_object_id/entra_login_object_ids. This grants sign-in rights to OTHER accounts and requires the deploy principal to hold Microsoft.Authorization/roleAssignments/write on the VM scope (beyond Contributor). When false (default), the local admin password still provides access and the emitted entra_login_grant_commands can be run manually by a privileged operator."
}

variable "search_sku" {
  type        = string
  default     = "basic"
  description = "Azure AI Search SKU."
}

variable "reasoning_model_deployment_name" {
  type        = string
  default     = "gpt-5.4-mini"
  description = "Deployment name for the reasoning model used by agents."
}

variable "reasoning_model_name" {
  type        = string
  default     = "gpt-5.4-mini"
  description = "OpenAI model name for reasoning deployment."
}

variable "reasoning_model_version" {
  type        = string
  default     = "2026-03-17"
  description = "OpenAI model version for reasoning deployment."
}

variable "reasoning_model_sku" {
  type        = string
  default     = "GlobalStandard"
  description = "SKU name for the reasoning deployment."
}

variable "reasoning_model_capacity" {
  type        = number
  default     = 100
  description = "Capacity for the reasoning deployment SKU."
}

variable "embedding_model_deployment_name" {
  type        = string
  default     = "text-embedding-3-small"
  description = "Deployment name for embeddings model used for index hydration."
}

variable "embedding_model_name" {
  type        = string
  default     = "text-embedding-3-small"
  description = "OpenAI model name for embedding deployment."
}

variable "embedding_model_version" {
  type        = string
  default     = "1"
  description = "OpenAI model version for embedding deployment."
}

variable "embedding_model_sku" {
  type        = string
  default     = "GlobalStandard"
  description = "SKU name for the embedding deployment."
}

variable "embedding_model_capacity" {
  type        = number
  default     = 10
  description = "Capacity for the embedding deployment SKU."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Optional tags map."
}

variable "storage_replication_type" {
  type        = string
  default     = "LRS"
  description = "Replication type for deployment state storage account (LRS, ZRS, GRS, RAGRS)."
}

variable "deployment_state_container_name" {
  type        = string
  default     = "deployment-state"
  description = "Blob container name used to persist deploy state and backend data archives."
}

variable "workload_management_group_id" {
  type        = string
  default     = ""
  description = "Management group ID whose subscriptions the app may collect and add to Azure Service Groups when management-group RBAC is enabled. The VM identity is granted Reader plus a least-privilege Service Group member-writer custom role at this scope. Defaults to the tenant-root management group (== tenant ID) when empty. NOTE: assigning at this scope requires the deploy principal to hold Microsoft.Authorization/roleAssignments/write at the management group."
}

variable "enable_workload_management_group_rbac" {
  type        = bool
  default     = true
  description = "Whether to additionally grant management-group Reader and place the Service Group member-writer role at that scope. The VM identity always receives Reader and member-writer access at the current subscription when this is disabled. Only takes effect when assign_rbac_roles = true."
}

variable "assign_rbac_roles" {
  type        = bool
  default     = false
  description = "Whether Terraform assigns the VM (and service-to-service) managed-identity data-plane roles plus subscription/management-group Reader and the Service Group member-writer custom role. These require the deploy principal to hold Microsoft.Authorization/roleAssignments/write (Owner or User Access Administrator), which exceeds Contributor. When false (default), the deployment runs with Contributor only and Terraform emits ready-to-run rbac_grant_commands for a privileged operator to apply after deployment."
}
