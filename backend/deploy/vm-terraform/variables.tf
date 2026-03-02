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
  description = "Linux VM size."
}

variable "vm_zone" {
  type        = string
  default     = ""
  description = "Optional availability zone for the VM (e.g., 1, 2, or 3)."
}

variable "admin_username" {
  type        = string
  default     = "azureuser"
  description = "VM admin SSH username."
}

variable "entra_admin_object_id" {
  type        = string
  default     = ""
  description = "Optional Entra object id to grant VM Administrator Login. Defaults to current az login principal."
}

variable "search_sku" {
  type        = string
  default     = "basic"
  description = "Azure AI Search SKU."
}

variable "reasoning_model_deployment_name" {
  type        = string
  default     = "gpt-4.1"
  description = "Deployment name for the reasoning model used by agents."
}

variable "reasoning_model_name" {
  type        = string
  default     = "gpt-4.1"
  description = "OpenAI model name for reasoning deployment."
}

variable "reasoning_model_version" {
  type        = string
  default     = "2025-04-14"
  description = "OpenAI model version for reasoning deployment."
}

variable "reasoning_model_sku" {
  type        = string
  default     = "GlobalStandard"
  description = "SKU name for the reasoning deployment."
}

variable "reasoning_model_capacity" {
  type        = number
  default     = 10
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
