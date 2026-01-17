locals {
  name = replace(lower(var.prefix), "/[^a-z0-9-]/", "")
}

resource "azurerm_resource_group" "rg" {
  name     = "${local.name}-rg"
  location = var.location
  tags     = var.tags
}

# -----------------------
# Networking (simple + cheap)
# -----------------------
resource "azurerm_virtual_network" "vnet" {
  name                = "${local.name}-vnet"
  address_space       = ["10.10.0.0/16"]
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  tags                = var.tags
}

resource "azurerm_subnet" "aks" {
  name                 = "${local.name}-snet-aks"
  resource_group_name  = azurerm_resource_group.rg.name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = ["10.10.1.0/24"]
}

# -----------------------
# ACR (Basic) - useful for AKS images, cheap tier
# -----------------------
resource "random_string" "acr_suffix" {
  length  = 6
  upper   = false
  special = false
}

resource "azurerm_container_registry" "acr" {
  name                = replace("${local.name}acr${random_string.acr_suffix.result}", "-", "")
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = var.tags
}

# -----------------------
# AKS (3 nodes across 3 AZs)
# - kubenet is cheaper/simpler than Azure CNI
# - system node pool spread across zones 1/2/3
# -----------------------
resource "azurerm_kubernetes_cluster" "aks" {
  name                = "${local.name}-aks"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  dns_prefix          = "${local.name}-dns"

  kubernetes_version  = var.kubernetes_version

  identity {
    type = "SystemAssigned"
  }

  default_node_pool {
    name                 = "sysnp"
    vm_size              = var.node_vm_size
    node_count           = var.node_count
    vnet_subnet_id       = azurerm_subnet.aks.id
    orchestrator_version = var.kubernetes_version

    availability_zones   = ["1", "2", "3"]

    # keep it cheap (no autoscaling by default)
    enable_auto_scaling  = false
    max_pods             = 30
    os_disk_size_gb      = 50
    type                 = "VirtualMachineScaleSets"
  }

  network_profile {
    network_plugin = "kubenet"
    load_balancer_sku = "standard"
    outbound_type  = "loadBalancer"
  }

  tags = var.tags
}

# Allow AKS to pull from ACR (no admin creds)
resource "azurerm_role_assignment" "aks_acr_pull" {
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.aks.kubelet_identity[0].object_id
}

# -----------------------
# Storage Account + Blob container
# -----------------------
resource "random_string" "storage_suffix" {
  length  = 8
  upper   = false
  special = false
}

resource "azurerm_storage_account" "sa" {
  name                     = substr(replace("${local.name}sa${random_string.storage_suffix.result}", "-", ""), 0, 24)
  resource_group_name      = azurerm_resource_group.rg.name
  location                 = azurerm_resource_group.rg.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  access_tier              = "Hot"

  allow_nested_items_to_be_public = false
  min_tls_version                = "TLS1_2"

  tags = var.tags
}

resource "azurerm_storage_container" "blob" {
  name                  = "app-blob"
  storage_account_name  = azurerm_storage_account.sa.name
  container_access_type = "private"
}

# -----------------------
# Azure SQL (serverless to reduce cost when idle)
# -----------------------
resource "random_password" "sql_admin_password" {
  length  = 20
  special = true
}

resource "azurerm_mssql_server" "sql" {
  name                         = "${local.name}-sql"
  resource_group_name          = azurerm_resource_group.rg.name
  location                     = azurerm_resource_group.rg.location
  version                      = "12.0"
  administrator_login          = var.sql_admin_username
  administrator_login_password = random_password.sql_admin_password.result
  minimum_tls_version          = "1.2"
  public_network_access_enabled = true

  tags = var.tags
}

# Allow Azure services (incl. AKS egress) to reach SQL (simple/cheap default)
resource "azurerm_mssql_firewall_rule" "allow_azure" {
  name             = "AllowAzureServices"
  server_id        = azurerm_mssql_server.sql.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

resource "azurerm_mssql_database" "sqldb" {
  name      = "${local.name}-db"
  server_id = azurerm_mssql_server.sql.id

  # Serverless General Purpose (Gen5) - can auto-pause
  sku_name                 = "GP_S_Gen5_1"
  auto_pause_delay_in_minutes = 60     # pauses after 60 mins idle
  min_capacity             = 0.5       # cheapest min
  max_size_gb              = 32

  zone_redundant           = false
  read_scale               = false

  tags = var.tags
}

# -----------------------
# Cosmos DB (serverless)
# -----------------------
resource "random_string" "cosmos_suffix" {
  length  = 6
  upper   = false
  special = false
}

resource "azurerm_cosmosdb_account" "cosmos" {
  name                = "${local.name}-cosmos-${random_string.cosmos_suffix.result}"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name

  offer_type          = "Standard"
  kind                = "GlobalDocumentDB"

  # cheapest behavior for a dev-ish setup
  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = azurerm_resource_group.rg.location
    failover_priority = 0
  }

  capabilities {
    name = "EnableServerless"
  }

  enable_free_tier = true

  public_network_access_enabled = true

  tags = var.tags
}

resource "azurerm_cosmosdb_sql_database" "cosmosdb" {
  name                = "${local.name}-cosmosdb"
  resource_group_name = azurerm_resource_group.rg.name
  account_name        = azurerm_cosmosdb_account.cosmos.name
}

resource "azurerm_cosmosdb_sql_container" "cosmos_container" {
  name                = "items"
  resource_group_name = azurerm_resource_group.rg.name
  account_name        = azurerm_cosmosdb_account.cosmos.name
  database_name       = azurerm_cosmosdb_sql_database.cosmosdb.name

  partition_key_path = "/pk"

  indexing_policy {
    indexing_mode = "consistent"
    included_path { path = "/*" }
  }
}

# -----------------------
# API Gateway: API Management (Consumption tier)
# -----------------------
resource "azurerm_api_management" "apim" {
  name                = "${local.name}-apim"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name

  publisher_name  = "Platform Team"
  publisher_email = "platform@example.com"

  sku_name = "Consumption_0"

  tags = var.tags
}

# A sample API + operation (you can wire it to your AKS ingress later)
resource "azurerm_api_management_api" "sample_api" {
  name                = "sample"
  resource_group_name = azurerm_resource_group.rg.name
  api_management_name = azurerm_api_management.apim.name

  revision     = "1"
  display_name = "Sample API"
  path         = "sample"
  protocols    = ["https"]

  subscription_required = false
}

resource "azurerm_api_management_api_operation" "hello" {
  operation_id        = "hello"
  api_name            = azurerm_api_management_api.sample_api.name
  api_management_name = azurerm_api_management.apim.name
  resource_group_name = azurerm_resource_group.rg.name

  display_name = "Hello"
  method       = "GET"
  url_template = "/hello"

  response {
    status_code = 200
  }
}
