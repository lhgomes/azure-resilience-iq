output "resource_group" {
  value = azurerm_resource_group.rg.name
}

output "aks_name" {
  value = azurerm_kubernetes_cluster.aks.name
}

output "aks_kubeconfig_raw" {
  value     = azurerm_kubernetes_cluster.aks.kube_config_raw
  sensitive = true
}

output "acr_login_server" {
  value = azurerm_container_registry.acr.login_server
}

output "storage_account_name" {
  value = azurerm_storage_account.sa.name
}

output "blob_container_name" {
  value = azurerm_storage_container.blob.name
}

output "sql_server_fqdn" {
  value = azurerm_mssql_server.sql.fully_qualified_domain_name
}

output "sql_admin_username" {
  value = var.sql_admin_username
}

output "sql_admin_password" {
  value     = random_password.sql_admin_password.result
  sensitive = true
}

output "cosmos_account_name" {
  value = azurerm_cosmosdb_account.cosmos.name
}

output "apim_gateway_url" {
  value = azurerm_api_management.apim.gateway_url
}
