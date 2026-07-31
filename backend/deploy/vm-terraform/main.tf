resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
}

resource "random_password" "vm_admin" {
  length           = 24
  special          = true
  override_special = "!@#%_-"
}

data "azurerm_client_config" "current" {}

locals {
  prefix              = "${var.project_name}-${var.environment}"
  resource_group_name = var.resource_group_name != "" ? var.resource_group_name : "rg-${local.prefix}"

  vnet_name                       = "vnet-${local.prefix}"
  subnet_name                     = "snet-app"
  pe_subnet_name                  = "snet-private-endpoints"
  bastion_subnet_name             = "AzureBastionSubnet"
  nsg_name                        = "nsg-${local.prefix}"
  bastion_pip_name                = "pip-bastion-${local.prefix}"
  bastion_name                    = "bas-${local.prefix}"
  nic_name                        = "nic-${local.prefix}"
  vm_name                         = "vm-${local.prefix}"
  foundry_hub_name                = "fdh-${local.prefix}-${random_string.suffix.result}"
  foundry_account_name            = "fdh-${local.prefix}-${random_string.suffix.result}"
  foundry_subdomain               = substr(replace("fdry${var.project_name}${var.environment}${random_string.suffix.result}", "-", ""), 0, 63)
  foundry_project_name            = "fdp-${local.prefix}-${random_string.suffix.result}"
  search_name                     = substr(replace("srch${var.project_name}${var.environment}${random_string.suffix.result}", "-", ""), 0, 60)
  storage_account_name            = substr(replace("st${var.project_name}${var.environment}${random_string.suffix.result}", "-", ""), 0, 24)
  deployment_state_prefix         = "${var.project_name}/${var.environment}"
  entra_admin_object_id           = var.entra_admin_object_id != "" ? var.entra_admin_object_id : data.azurerm_client_config.current.object_id
  workload_management_group_id    = var.workload_management_group_id != "" ? var.workload_management_group_id : data.azurerm_client_config.current.tenant_id
  workload_management_group_scope = "/providers/Microsoft.Management/managementGroups/${local.workload_management_group_id}"
  service_group_member_rbac_scope = var.enable_workload_management_group_rbac ? local.workload_management_group_scope : "/subscriptions/${data.azurerm_client_config.current.subscription_id}"

  common_tags = merge(
    {
      project     = var.project_name
      environment = var.environment
      managed_by  = "terraform"
    },
    var.tags
  )
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

resource "azurerm_subnet" "bastion" {
  count = var.enable_bastion ? 1 : 0

  name                 = local.bastion_subnet_name
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = ["10.20.3.0/26"]
}

resource "azurerm_network_security_group" "this" {
  name                = local.nsg_name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.common_tags
}

resource "azurerm_network_security_rule" "allow_bastion_rdp" {
  count = var.enable_bastion ? 1 : 0

  name                        = "allow-bastion-rdp"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "3389"
  source_address_prefix       = azurerm_subnet.bastion[0].address_prefixes[0]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.this.name
  network_security_group_name = azurerm_network_security_group.this.name
}

resource "azurerm_network_security_rule" "allow_bastion_ssh" {
  count = var.enable_bastion ? 1 : 0

  name                        = "allow-bastion-ssh"
  priority                    = 105
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefix       = azurerm_subnet.bastion[0].address_prefixes[0]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.this.name
  network_security_group_name = azurerm_network_security_group.this.name
}

resource "azurerm_subnet_network_security_group_association" "this" {
  subnet_id                 = azurerm_subnet.this.id
  network_security_group_id = azurerm_network_security_group.this.id
}

resource "azurerm_public_ip" "bastion" {
  count = var.enable_bastion ? 1 : 0

  name                = local.bastion_pip_name
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

resource "azurerm_bastion_host" "this" {
  count = var.enable_bastion ? 1 : 0

  name                = local.bastion_name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "Standard"
  tunneling_enabled   = true
  tags                = local.common_tags

  ip_configuration {
    name                 = "configuration"
    subnet_id            = azurerm_subnet.bastion[0].id
    public_ip_address_id = azurerm_public_ip.bastion[0].id
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
  }
}

resource "azurerm_windows_virtual_machine" "this" {
  name                = local.vm_name
  computer_name       = substr(replace(local.vm_name, "-", ""), 0, 15)
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  size                = var.vm_size
  zone                = var.vm_zone != "" ? var.vm_zone : null
  admin_username      = var.admin_username
  admin_password      = random_password.vm_admin.result
  network_interface_ids = [
    azurerm_network_interface.this.id,
  ]
  tags = local.common_tags

  identity {
    type = "SystemAssigned"
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Premium_LRS"
  }

  source_image_reference {
    publisher = "MicrosoftWindowsServer"
    offer     = "WindowsServer"
    sku       = "2022-datacenter-azure-edition"
    version   = "latest"
  }
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

  lifecycle {
    create_before_destroy = true
    ignore_changes        = [rai_policy_name]
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

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_search_shared_private_link_service" "foundry_embedding" {
  name               = "spl-foundry-embedding-${local.prefix}"
  search_service_id  = azurerm_search_service.this.id
  target_resource_id = azurerm_cognitive_account.this.id
  subresource_name   = "openai_account"
  request_message    = "Azure AI Search query vectorization"
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

  network_rules {
    default_action = "Deny"
    bypass         = ["AzureServices"]
  }
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

resource "azurerm_virtual_machine_extension" "aad_login" {
  name                       = "AADLoginForWindows"
  virtual_machine_id         = azurerm_windows_virtual_machine.this.id
  publisher                  = "Microsoft.Azure.ActiveDirectory"
  type                       = "AADLoginForWindows"
  type_handler_version       = "2.2"
  auto_upgrade_minor_version = true
}

resource "azurerm_role_assignment" "entra_vm_admin_login" {
  scope                = azurerm_windows_virtual_machine.this.id
  role_definition_name = "Virtual Machine Administrator Login"
  principal_id         = local.entra_admin_object_id
}

resource "azurerm_role_assignment" "vm_foundry_hub_user" {
  scope                = azurerm_cognitive_account.this.id
  role_definition_name = "Foundry User"
  principal_id         = azurerm_windows_virtual_machine.this.identity[0].principal_id
}

resource "azurerm_role_assignment" "vm_foundry_openai_user" {
  scope                = azurerm_cognitive_account.this.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_windows_virtual_machine.this.identity[0].principal_id
}

resource "azurerm_role_assignment" "vm_foundry_project_user" {
  scope                = azurerm_cognitive_account_project.this.id
  role_definition_name = "Foundry User"
  principal_id         = azurerm_windows_virtual_machine.this.identity[0].principal_id
}

resource "azurerm_role_assignment" "foundry_project_identity_hub_user" {
  scope                = azurerm_cognitive_account.this.id
  role_definition_name = "Foundry User"
  principal_id         = azurerm_cognitive_account_project.this.identity[0].principal_id
}

resource "azurerm_role_assignment" "search_foundry_openai_user" {
  scope                = azurerm_cognitive_account.this.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_search_service.this.identity[0].principal_id
}

resource "azurerm_role_assignment" "foundry_project_search_service_contributor" {
  scope                = azurerm_search_service.this.id
  role_definition_name = "Search Service Contributor"
  principal_id         = azurerm_cognitive_account_project.this.identity[0].principal_id
}

resource "azurerm_role_assignment" "foundry_project_search_index_data_contributor" {
  scope                = azurerm_search_service.this.id
  role_definition_name = "Search Index Data Contributor"
  principal_id         = azurerm_cognitive_account_project.this.identity[0].principal_id
}

resource "azurerm_role_assignment" "vm_search_service_contributor" {
  scope                = azurerm_search_service.this.id
  role_definition_name = "Search Service Contributor"
  principal_id         = azurerm_windows_virtual_machine.this.identity[0].principal_id
}

resource "azurerm_role_assignment" "vm_search_index_data_contributor" {
  scope                = azurerm_search_service.this.id
  role_definition_name = "Search Index Data Contributor"
  principal_id         = azurerm_windows_virtual_machine.this.identity[0].principal_id
}

resource "azurerm_role_assignment" "vm_storage_blob_data_contributor" {
  scope                = azurerm_storage_account.this.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_windows_virtual_machine.this.identity[0].principal_id
}

# --- Service Group + collection permissions for the VM identity -------------
# The collector reads resources and role assignments in the current subscription,
# with optional management-group Reader access for broader collection. The
# Service Group applier writes Microsoft.Relationships/serviceGroupMember links
# on member resources (a linked action authorized on the member scope). The
# least-privilege writer is always granted: at management-group scope when broad
# workload RBAC is enabled, otherwise at the current subscription. Creating a
# Service Group needs no grant here (the creator is auto-assigned Service Group
# Administrator, which also lets the app read/import that SG through Resource
# Graph). Reading Service Groups
# created ELSEWHERE needs Microsoft.Management/serviceGroups/read on them;
# assigning "Service Group Reader" at the tenant-root SG is the broadest, OPTIONAL
# way to get that -- see the service_group_root_reader_grant_command output.

resource "azurerm_role_assignment" "vm_subscription_reader" {
  scope                = "/subscriptions/${data.azurerm_client_config.current.subscription_id}"
  role_definition_name = "Reader"
  principal_id         = azurerm_windows_virtual_machine.this.identity[0].principal_id
}

resource "azurerm_role_assignment" "vm_workload_reader" {
  count                = var.enable_workload_management_group_rbac ? 1 : 0
  scope                = local.workload_management_group_scope
  role_definition_name = "Reader"
  principal_id         = azurerm_windows_virtual_machine.this.identity[0].principal_id
}

resource "azurerm_role_definition" "service_group_member_writer" {
  name        = "Service Group Member Writer (${local.prefix})"
  scope       = local.service_group_member_rbac_scope
  description = "Least-privilege role allowing write/read/delete of serviceGroupMember relationships so the workload app can attach resources to Azure Service Groups."

  permissions {
    actions = [
      "Microsoft.Relationships/serviceGroupMember/write",
      "Microsoft.Relationships/serviceGroupMember/read",
      "Microsoft.Relationships/serviceGroupMember/delete",
    ]
    not_actions = []
  }

  assignable_scopes = [local.service_group_member_rbac_scope]
}

resource "azurerm_role_assignment" "vm_service_group_member_writer" {
  scope              = local.service_group_member_rbac_scope
  role_definition_id = azurerm_role_definition.service_group_member_writer.role_definition_resource_id
  principal_id       = azurerm_windows_virtual_machine.this.identity[0].principal_id
}
