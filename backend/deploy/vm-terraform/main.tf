resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
}

resource "tls_private_key" "vm_admin" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

data "azurerm_client_config" "current" {}

data "http" "operator_public_ip" {
  count = !var.private_only && (length(var.admin_allowed_cidrs) == 0 || length(var.app_allowed_cidrs) == 0) ? 1 : 0
  url   = "https://api.ipify.org"

  request_headers = {
    Accept = "text/plain"
  }
}

locals {
  prefix              = "${var.project_name}-${var.environment}"
  resource_group_name = var.resource_group_name != "" ? var.resource_group_name : "rg-${local.prefix}"

  vnet_name               = "vnet-${local.prefix}"
  subnet_name             = "snet-app"
  pe_subnet_name          = "snet-private-endpoints"
  nsg_name                = "nsg-${local.prefix}"
  pip_name                = "pip-${local.prefix}"
  nic_name                = "nic-${local.prefix}"
  vm_name                 = "vm-${local.prefix}"
  foundry_hub_name        = "fdh-${local.prefix}-${random_string.suffix.result}"
  foundry_account_name    = "fdh-${local.prefix}-${random_string.suffix.result}"
  foundry_subdomain       = substr(replace("fdry${var.project_name}${var.environment}${random_string.suffix.result}", "-", ""), 0, 63)
  foundry_project_name    = "fdp-${local.prefix}-${random_string.suffix.result}"
  search_name             = substr(replace("srch${var.project_name}${var.environment}${random_string.suffix.result}", "-", ""), 0, 60)
  storage_account_name    = substr(replace("st${var.project_name}${var.environment}${random_string.suffix.result}", "-", ""), 0, 24)
  deployment_state_prefix = "${var.project_name}/${var.environment}"
  entra_admin_object_id   = var.entra_admin_object_id != "" ? var.entra_admin_object_id : data.azurerm_client_config.current.object_id
  operator_public_ip      = length(data.http.operator_public_ip) > 0 ? trimspace(data.http.operator_public_ip[0].response_body) : ""
  operator_public_cidr    = local.operator_public_ip != "" ? "${local.operator_public_ip}/32" : ""

  provided_admin_allowed_cidrs = [for cidr in var.admin_allowed_cidrs : trimspace(cidr) if trimspace(cidr) != ""]
  provided_app_allowed_cidrs   = [for cidr in var.app_allowed_cidrs : trimspace(cidr) if trimspace(cidr) != ""]

  effective_admin_allowed_cidrs = length(local.provided_admin_allowed_cidrs) > 0 ? local.provided_admin_allowed_cidrs : (
    local.operator_public_cidr != "" ? [local.operator_public_cidr] : []
  )
  effective_app_allowed_cidrs = length(local.provided_app_allowed_cidrs) > 0 ? local.provided_app_allowed_cidrs : (
    local.operator_public_cidr != "" ? [local.operator_public_cidr] : []
  )

  common_tags = merge(
    {
      project     = var.project_name
      environment = var.environment
      managed_by  = "terraform"
    },
    var.tags
  )
}

resource "terraform_data" "validate_public_ingress_cidrs" {
  input = true

  lifecycle {
    precondition {
      condition = var.private_only || (
        length(local.effective_admin_allowed_cidrs) > 0 &&
        length(local.effective_app_allowed_cidrs) > 0
      )
      error_message = "No effective CIDR allowlists were resolved while private_only=false. Provide admin_allowed_cidrs and app_allowed_cidrs explicitly, or ensure the Terraform runner can reach https://api.ipify.org for auto-discovery."
    }
  }
}

resource "azurerm_resource_group" "this" {
  name     = local.resource_group_name
  location = var.location
  tags     = local.common_tags
}

resource "azurerm_virtual_network" "this" {
  name                = local.vnet_name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  address_space       = ["10.20.0.0/16"]
  tags                = local.common_tags
}

resource "azurerm_subnet" "this" {
  name                 = local.subnet_name
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = ["10.20.1.0/24"]
}

resource "azurerm_subnet" "private_endpoints" {
  name                              = local.pe_subnet_name
  resource_group_name               = azurerm_resource_group.this.name
  virtual_network_name              = azurerm_virtual_network.this.name
  address_prefixes                  = ["10.20.2.0/24"]
  private_endpoint_network_policies = "Disabled"
}

resource "azurerm_network_security_group" "this" {
  name                = local.nsg_name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.common_tags
}

resource "azurerm_network_security_rule" "allow_ssh" {
  count                       = !var.private_only && length(local.effective_admin_allowed_cidrs) > 0 ? 1 : 0
  name                        = "allow-ssh"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefixes     = local.effective_admin_allowed_cidrs
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.this.name
  network_security_group_name = azurerm_network_security_group.this.name
}

resource "azurerm_network_security_rule" "allow_http" {
  count                       = !var.private_only && length(local.effective_app_allowed_cidrs) > 0 ? 1 : 0
  name                        = "allow-http"
  priority                    = 110
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "80"
  source_address_prefixes     = local.effective_app_allowed_cidrs
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.this.name
  network_security_group_name = azurerm_network_security_group.this.name
}

resource "azurerm_network_security_rule" "allow_https" {
  count                       = !var.private_only && length(local.effective_app_allowed_cidrs) > 0 ? 1 : 0
  name                        = "allow-https"
  priority                    = 120
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "443"
  source_address_prefixes     = local.effective_app_allowed_cidrs
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.this.name
  network_security_group_name = azurerm_network_security_group.this.name
}

resource "azurerm_subnet_network_security_group_association" "this" {
  subnet_id                 = azurerm_subnet.this.id
  network_security_group_id = azurerm_network_security_group.this.id
}

resource "azurerm_public_ip" "this" {
  count               = var.private_only ? 0 : 1
  name                = local.pip_name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.common_tags

  lifecycle {
    ignore_changes = [
      ip_tags,
      zones,
    ]
  }
}

resource "azurerm_network_interface" "this" {
  name                = local.nic_name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.common_tags

  ip_configuration {
    name                          = "ipconfig1"
    subnet_id                     = azurerm_subnet.this.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = var.private_only ? null : azurerm_public_ip.this[0].id
  }
}

resource "azurerm_linux_virtual_machine" "this" {
  name                = local.vm_name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  size                = var.vm_size
  zone                = var.vm_zone != "" ? var.vm_zone : null
  admin_username      = var.admin_username
  network_interface_ids = [
    azurerm_network_interface.this.id,
  ]
  tags = local.common_tags

  identity {
    type = "SystemAssigned"
  }

  admin_ssh_key {
    username   = var.admin_username
    public_key = tls_private_key.vm_admin.public_key_openssh
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Premium_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts-gen2"
    version   = "latest"
  }

  disable_password_authentication = true
}

resource "azurerm_cognitive_account" "this" {
  name                          = local.foundry_account_name
  location                      = azurerm_resource_group.this.location
  resource_group_name           = azurerm_resource_group.this.name
  kind                          = "AIServices"
  sku_name                      = "S0"
  custom_subdomain_name         = local.foundry_subdomain
  project_management_enabled    = true
  local_auth_enabled            = false
  public_network_access_enabled = false
  dynamic_throttling_enabled    = false
  tags                          = local.common_tags

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_cognitive_account_project" "this" {
  name                 = local.foundry_project_name
  location             = azurerm_resource_group.this.location
  cognitive_account_id = azurerm_cognitive_account.this.id
  tags                 = local.common_tags

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_cognitive_deployment" "reasoning" {
  name                 = var.reasoning_model_deployment_name
  cognitive_account_id = azurerm_cognitive_account.this.id

  model {
    format  = "OpenAI"
    name    = var.reasoning_model_name
    version = var.reasoning_model_version
  }

  sku {
    name     = var.reasoning_model_sku
    capacity = var.reasoning_model_capacity
  }
}

resource "azurerm_cognitive_deployment" "embedding" {
  name                 = var.embedding_model_deployment_name
  cognitive_account_id = azurerm_cognitive_account.this.id

  model {
    format  = "OpenAI"
    name    = var.embedding_model_name
    version = var.embedding_model_version
  }

  sku {
    name     = var.embedding_model_sku
    capacity = var.embedding_model_capacity
  }
}

resource "azurerm_search_service" "this" {
  name                          = local.search_name
  resource_group_name           = azurerm_resource_group.this.name
  location                      = azurerm_resource_group.this.location
  sku                           = var.search_sku
  replica_count                 = 1
  partition_count               = 1
  local_authentication_enabled  = false
  public_network_access_enabled = false
  tags                          = local.common_tags
}

resource "azurerm_storage_account" "this" {
  name                            = local.storage_account_name
  resource_group_name             = azurerm_resource_group.this.name
  location                        = azurerm_resource_group.this.location
  account_tier                    = "Standard"
  account_replication_type        = var.storage_replication_type
  account_kind                    = "StorageV2"
  public_network_access_enabled   = false
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
  min_tls_version                 = "TLS1_2"
  tags                            = local.common_tags
}

resource "azurerm_private_dns_zone" "foundry" {
  name                = "privatelink.services.ai.azure.com"
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.common_tags
}

resource "azurerm_private_dns_zone" "foundry_cognitiveservices" {
  name                = "privatelink.cognitiveservices.azure.com"
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.common_tags
}

resource "azurerm_private_dns_zone" "foundry_openai" {
  name                = "privatelink.openai.azure.com"
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.common_tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "foundry" {
  name                  = "link-foundry-${local.prefix}"
  private_dns_zone_name = azurerm_private_dns_zone.foundry.name
  resource_group_name   = azurerm_resource_group.this.name
  virtual_network_id    = azurerm_virtual_network.this.id
  registration_enabled  = false
  tags                  = local.common_tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "foundry_cognitiveservices" {
  name                  = "link-foundry-cognitiveservices-${local.prefix}"
  private_dns_zone_name = azurerm_private_dns_zone.foundry_cognitiveservices.name
  resource_group_name   = azurerm_resource_group.this.name
  virtual_network_id    = azurerm_virtual_network.this.id
  registration_enabled  = false
  tags                  = local.common_tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "foundry_openai" {
  name                  = "link-foundry-openai-${local.prefix}"
  private_dns_zone_name = azurerm_private_dns_zone.foundry_openai.name
  resource_group_name   = azurerm_resource_group.this.name
  virtual_network_id    = azurerm_virtual_network.this.id
  registration_enabled  = false
  tags                  = local.common_tags
}

resource "azurerm_private_endpoint" "foundry" {
  name                = "pe-foundry-${local.prefix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  subnet_id           = azurerm_subnet.private_endpoints.id
  tags                = local.common_tags

  depends_on = [
    azurerm_cognitive_account_project.this,
    azurerm_cognitive_deployment.reasoning,
    azurerm_cognitive_deployment.embedding,
  ]

  private_service_connection {
    name                           = "psc-foundry-${local.prefix}"
    private_connection_resource_id = azurerm_cognitive_account.this.id
    subresource_names              = ["account"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name = "foundry-dns"
    private_dns_zone_ids = [
      azurerm_private_dns_zone.foundry.id,
      azurerm_private_dns_zone.foundry_cognitiveservices.id,
      azurerm_private_dns_zone.foundry_openai.id,
    ]
  }
}

resource "azurerm_private_dns_zone" "search" {
  name                = "privatelink.search.windows.net"
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.common_tags
}

resource "azurerm_private_dns_zone" "blob" {
  name                = "privatelink.blob.core.windows.net"
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.common_tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "search" {
  name                  = "link-search-${local.prefix}"
  private_dns_zone_name = azurerm_private_dns_zone.search.name
  resource_group_name   = azurerm_resource_group.this.name
  virtual_network_id    = azurerm_virtual_network.this.id
  registration_enabled  = false
  tags                  = local.common_tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "blob" {
  name                  = "link-blob-${local.prefix}"
  private_dns_zone_name = azurerm_private_dns_zone.blob.name
  resource_group_name   = azurerm_resource_group.this.name
  virtual_network_id    = azurerm_virtual_network.this.id
  registration_enabled  = false
  tags                  = local.common_tags
}

resource "azurerm_private_endpoint" "search" {
  name                = "pe-search-${local.prefix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  subnet_id           = azurerm_subnet.private_endpoints.id
  tags                = local.common_tags

  private_service_connection {
    name                           = "psc-search-${local.prefix}"
    private_connection_resource_id = azurerm_search_service.this.id
    subresource_names              = ["searchService"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "search-dns"
    private_dns_zone_ids = [azurerm_private_dns_zone.search.id]
  }
}

resource "azurerm_private_endpoint" "blob" {
  name                = "pe-blob-${local.prefix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  subnet_id           = azurerm_subnet.private_endpoints.id
  tags                = local.common_tags

  private_service_connection {
    name                           = "psc-blob-${local.prefix}"
    private_connection_resource_id = azurerm_storage_account.this.id
    subresource_names              = ["blob"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "blob-dns"
    private_dns_zone_ids = [azurerm_private_dns_zone.blob.id]
  }
}

resource "azurerm_virtual_machine_extension" "aad_ssh_login" {
  name                 = "AADSSHLoginForLinux"
  virtual_machine_id   = azurerm_linux_virtual_machine.this.id
  publisher            = "Microsoft.Azure.ActiveDirectory"
  type                 = "AADSSHLoginForLinux"
  type_handler_version = "1.0"
}

resource "azurerm_role_assignment" "entra_vm_admin_login" {
  scope                = azurerm_linux_virtual_machine.this.id
  role_definition_name = "Virtual Machine Administrator Login"
  principal_id         = local.entra_admin_object_id
}

resource "azurerm_role_assignment" "vm_foundry_hub_user" {
  scope                = azurerm_cognitive_account.this.id
  role_definition_name = "Foundry User"
  principal_id         = azurerm_linux_virtual_machine.this.identity[0].principal_id
}

resource "azurerm_role_assignment" "vm_foundry_openai_user" {
  scope                = azurerm_cognitive_account.this.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_linux_virtual_machine.this.identity[0].principal_id
}

resource "azurerm_role_assignment" "vm_foundry_project_user" {
  scope                = azurerm_cognitive_account_project.this.id
  role_definition_name = "Foundry User"
  principal_id         = azurerm_linux_virtual_machine.this.identity[0].principal_id
}

resource "azurerm_role_assignment" "foundry_project_identity_hub_user" {
  scope                = azurerm_cognitive_account.this.id
  role_definition_name = "Foundry User"
  principal_id         = azurerm_cognitive_account_project.this.identity[0].principal_id
}

resource "azurerm_role_assignment" "vm_search_service_contributor" {
  scope                = azurerm_search_service.this.id
  role_definition_name = "Search Service Contributor"
  principal_id         = azurerm_linux_virtual_machine.this.identity[0].principal_id
}

resource "azurerm_role_assignment" "vm_search_index_data_contributor" {
  scope                = azurerm_search_service.this.id
  role_definition_name = "Search Index Data Contributor"
  principal_id         = azurerm_linux_virtual_machine.this.identity[0].principal_id
}

resource "azurerm_role_assignment" "vm_storage_blob_data_contributor" {
  scope                = azurerm_storage_account.this.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_linux_virtual_machine.this.identity[0].principal_id
}
