variable "prefix" {
  description = "Prefix used for resource naming"
  type        = string
  default     = "lz-aks"
}

variable "location" {
  description = "Azure region"
  type        = string
  default     = "uksouth"
}

variable "kubernetes_version" {
  description = "AKS version (leave null to use Azure default)"
  type        = string
  default     = null
}

variable "node_vm_size" {
  description = "AKS node VM size (cheap option)"
  type        = string
  default     = "Standard_B2s"
}

variable "node_count" {
  description = "AKS node count (3 across 3 AZs)"
  type        = number
  default     = 3
}

variable "sql_admin_username" {
  description = "Azure SQL admin username"
  type        = string
  default     = "sqladminuser"
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {
    env = "dev"
  }
}
