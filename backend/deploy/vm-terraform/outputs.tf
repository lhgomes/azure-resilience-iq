output "resource_group_name" {
  value       = azurerm_resource_group.this.name
  description = "Resource group name."
}

output "vm_name" {
  value       = azurerm_windows_virtual_machine.this.name
  description = "Deployed VM name."
}

output "nic_name" {
  value       = azurerm_network_interface.this.name
  description = "Network interface name attached to the Windows virtual machine."
}

output "vm_admin_username" {
  value       = azurerm_windows_virtual_machine.this.admin_username
  description = "Local Windows administrator username."
}

output "bastion_name" {
  value       = try(azurerm_bastion_host.this[0].name, null)
  description = "Azure Bastion host used for RDP access to the private VM when enabled."
}

output "vm_private_ip" {
  value       = azurerm_network_interface.this.private_ip_address
  description = "VM private IP address."
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

output "foundry_hub_id" {
  value       = azurerm_cognitive_account.this.id
  description = "Azure Foundry account resource ID."
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

output "embedding_model_endpoint" {
  value       = "https://${azurerm_cognitive_account.this.custom_subdomain_name}.openai.azure.com"
  description = "Azure OpenAI-compatible endpoint used by the Search query vectorizer."
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
      --assignee-object-id ${azurerm_windows_virtual_machine.this.identity[0].principal_id} \
      --assignee-principal-type ServicePrincipal \
      --role "Service Group Reader" \
      --scope "/providers/Microsoft.Management/serviceGroups/${data.azurerm_client_config.current.tenant_id}"
  EOT
  description = "OPTIONAL manual tenant-admin step (cannot be run by the deploy principal): grants the VM identity Service Group Reader across the tenant so the app can read/import Service Groups it did NOT create. Reading/importing goes through Resource Graph -- Service Groups the app creates are already readable (the creator is auto-assigned Service Group Administrator), so this is unnecessary for them. Only needed to import externally-created Service Groups, and the tenant root is simply the broadest scope (a narrower Service Group scope also works). Requires a Global Admin with elevated access (Microsoft.Authorization/roleAssignments/write at tenant scope)."
}

output "rbac_grant_commands" {
  description = "When assign_rbac_roles = false, the exact az commands a privileged operator (Owner or User Access Administrator) runs AFTER the Contributor-only deployment to grant the VM and service managed identities their required roles. Empty guidance string when Terraform already assigned them."
  value = var.assign_rbac_roles ? "RBAC roles were assigned by Terraform (assign_rbac_roles = true); no manual role assignment is required." : join("\n", concat(
    [
      "# Run as a principal holding Microsoft.Authorization/roleAssignments/write (Owner or User Access Administrator).",
      "# --- VM managed identity ---------------------------------------------------",
      "az role assignment create --assignee-object-id ${azurerm_windows_virtual_machine.this.identity[0].principal_id} --assignee-principal-type ServicePrincipal --role \"Foundry User\" --scope \"${azurerm_cognitive_account.this.id}\"",
      "az role assignment create --assignee-object-id ${azurerm_windows_virtual_machine.this.identity[0].principal_id} --assignee-principal-type ServicePrincipal --role \"Cognitive Services OpenAI User\" --scope \"${azurerm_cognitive_account.this.id}\"",
      "az role assignment create --assignee-object-id ${azurerm_windows_virtual_machine.this.identity[0].principal_id} --assignee-principal-type ServicePrincipal --role \"Foundry User\" --scope \"${azurerm_cognitive_account_project.this.id}\"",
      "az role assignment create --assignee-object-id ${azurerm_windows_virtual_machine.this.identity[0].principal_id} --assignee-principal-type ServicePrincipal --role \"Search Service Contributor\" --scope \"${azurerm_search_service.this.id}\"",
      "az role assignment create --assignee-object-id ${azurerm_windows_virtual_machine.this.identity[0].principal_id} --assignee-principal-type ServicePrincipal --role \"Search Index Data Contributor\" --scope \"${azurerm_search_service.this.id}\"",
      "az role assignment create --assignee-object-id ${azurerm_windows_virtual_machine.this.identity[0].principal_id} --assignee-principal-type ServicePrincipal --role \"Storage Blob Data Contributor\" --scope \"${azurerm_storage_account.this.id}\"",
      "az role assignment create --assignee-object-id ${azurerm_windows_virtual_machine.this.identity[0].principal_id} --assignee-principal-type ServicePrincipal --role \"Reader\" --scope \"/subscriptions/${data.azurerm_client_config.current.subscription_id}\"",
      "# --- Azure AI Foundry project managed identity -----------------------------",
      "az role assignment create --assignee-object-id ${azurerm_cognitive_account_project.this.identity[0].principal_id} --assignee-principal-type ServicePrincipal --role \"Foundry User\" --scope \"${azurerm_cognitive_account.this.id}\"",
      "az role assignment create --assignee-object-id ${azurerm_cognitive_account_project.this.identity[0].principal_id} --assignee-principal-type ServicePrincipal --role \"Search Service Contributor\" --scope \"${azurerm_search_service.this.id}\"",
      "az role assignment create --assignee-object-id ${azurerm_cognitive_account_project.this.identity[0].principal_id} --assignee-principal-type ServicePrincipal --role \"Search Index Data Contributor\" --scope \"${azurerm_search_service.this.id}\"",
      "# --- Azure AI Search managed identity --------------------------------------",
      "az role assignment create --assignee-object-id ${azurerm_search_service.this.identity[0].principal_id} --assignee-principal-type ServicePrincipal --role \"Cognitive Services OpenAI User\" --scope \"${azurerm_cognitive_account.this.id}\"",
    ],
    var.enable_workload_management_group_rbac ? [
      "# --- VM managed identity: management-group Reader --------------------------",
      "az role assignment create --assignee-object-id ${azurerm_windows_virtual_machine.this.identity[0].principal_id} --assignee-principal-type ServicePrincipal --role \"Reader\" --scope \"${local.workload_management_group_scope}\"",
    ] : [],
    [
      "# --- Service Group member-writer custom role + assignment ------------------",
      "az role definition create --role-definition '{\"Name\": \"Service Group Member Writer (${local.prefix})\", \"Description\": \"Least-privilege serviceGroupMember write/read/delete for the workload app.\", \"Actions\": [\"Microsoft.Relationships/serviceGroupMember/write\", \"Microsoft.Relationships/serviceGroupMember/read\", \"Microsoft.Relationships/serviceGroupMember/delete\"], \"AssignableScopes\": [\"${local.service_group_member_rbac_scope}\"]}'",
      "az role assignment create --assignee-object-id ${azurerm_windows_virtual_machine.this.identity[0].principal_id} --assignee-principal-type ServicePrincipal --role \"Service Group Member Writer (${local.prefix})\" --scope \"${local.service_group_member_rbac_scope}\"",
    ]
  ))
}

output "entra_login_grant_commands" {
  description = "When assign_entra_login_roles = false, the az commands to grant human principals 'Virtual Machine Administrator Login' on the VM. Append --assignee-principal-type User or Group as appropriate. Empty guidance string when Terraform already assigned them."
  value = var.assign_entra_login_roles ? "Entra VM Administrator Login was assigned by Terraform (assign_entra_login_roles = true); no manual assignment is required." : join("\n", concat(
    ["# Grant sign-in with Entra credentials; add --assignee-principal-type User or Group per principal. The local admin password already provides access without this."],
    [for pid in local.entra_login_principal_ids : "az role assignment create --assignee-object-id ${pid} --role \"Virtual Machine Administrator Login\" --scope \"${azurerm_windows_virtual_machine.this.id}\""]
  ))
}
