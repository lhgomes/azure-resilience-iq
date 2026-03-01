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
  value       = azurerm_public_ip.this.ip_address
  description = "VM public IP address."
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
