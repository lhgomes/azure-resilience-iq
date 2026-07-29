output "resource_group_name" {
  value       = azurerm_resource_group.this.name
  description = "Resource group name."
}

output "vm_name" {
  value       = azurerm_linux_virtual_machine.this.name
  description = "Deployed VM name."
}

output "vm_admin_username" {
  value       = azurerm_linux_virtual_machine.this.admin_username
  description = "VM SSH admin username."
}

output "vm_public_ip" {
  value       = try(azurerm_public_ip.this[0].ip_address, "")
  description = "VM public IP address (empty when private_only=true)."
}

output "vm_private_ip" {
  value       = azurerm_network_interface.this.private_ip_address
  description = "VM private IP address."
}

output "effective_admin_allowed_cidrs" {
  value       = local.effective_admin_allowed_cidrs
  description = "Effective SSH allowlist CIDRs (provided values or auto-discovered /32)."
}

output "effective_app_allowed_cidrs" {
  value       = local.effective_app_allowed_cidrs
  description = "Effective app ingress allowlist CIDRs (provided values or auto-discovered /32)."
}

output "search_service_name" {
  value       = azurerm_search_service.this.name
  description = "Azure AI Search service name."
}

output "search_endpoint" {
  value       = "https://${azurerm_search_service.this.name}.search.windows.net"
  description = "Azure AI Search endpoint."
}

output "foundry_project_endpoint" {
  value       = "https://${azurerm_cognitive_account.this.custom_subdomain_name}.services.ai.azure.com/api/projects/${azurerm_cognitive_account_project.this.name}"
  description = "Generated Foundry project endpoint for SDK usage (new Foundry resource model)."
}

output "foundry_hub_name" {
  value       = azurerm_cognitive_account.this.name
  description = "Azure Foundry account name."
}

output "foundry_project_name" {
  value       = azurerm_cognitive_account_project.this.name
  description = "Azure AI Foundry project name."
}

output "reasoning_model_deployment_name" {
  value       = azurerm_cognitive_deployment.reasoning.name
  description = "Reasoning model deployment name."
}

output "embedding_model_deployment_name" {
  value       = azurerm_cognitive_deployment.embedding.name
  description = "Embedding model/deployment name used for hydration and runtime."
}

output "storage_account_name" {
  value       = azurerm_storage_account.this.name
  description = "Storage account used for deployment state persistence."
}

output "deployment_state_container_name" {
  value       = var.deployment_state_container_name
  description = "Blob container used for deployment state and backend data archives."
}

output "deployment_state_prefix" {
  value       = local.deployment_state_prefix
  description = "Blob prefix for deployment state and backend data archives."
}

output "service_group_root_reader_grant_command" {
  value       = <<-EOT
    az role assignment create \
      --assignee-object-id ${azurerm_linux_virtual_machine.this.identity[0].principal_id} \
      --assignee-principal-type ServicePrincipal \
      --role "Service Group Reader" \
      --scope "/providers/Microsoft.Management/serviceGroups/${data.azurerm_client_config.current.tenant_id}"
  EOT
  description = "OPTIONAL manual tenant-admin step (cannot be run by the deploy principal): grants the VM identity Service Group Reader across the tenant so the app can read/import Service Groups it did NOT create. Reading/importing goes through Resource Graph -- Service Groups the app creates are already readable (the creator is auto-assigned Service Group Administrator), so this is unnecessary for them. Only needed to import externally-created Service Groups, and the tenant root is simply the broadest scope (a narrower Service Group scope also works). Requires a Global Admin with elevated access (Microsoft.Authorization/roleAssignments/write at tenant scope)."
}
