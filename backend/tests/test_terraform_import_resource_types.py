from app.terraform.generator import TerraformResourceGenerator
from app.terraform.parser import TerraformParser


def test_resilience_terraform_resource_types_are_retained() -> None:
    terraform = '''
resource "azurerm_resource_group" "ai" {
  name     = "rg-ai"
  location = "uaenorth"
}

resource "azurerm_storage_account" "storage" {
  name                     = "resilientstorage"
  resource_group_name      = azurerm_resource_group.ai.name
  location                 = "westus2"
  account_tier             = "Standard"
  account_replication_type = "ZRS"
}

resource "azurerm_search_service" "primary" {
  name                = "search-primary"
  resource_group_name = azurerm_resource_group.ai.name
  location            = "uaenorth"
  sku                 = "standard"
  replica_count       = 2
}

resource "azurerm_search_service" "secondary" {
  name                = "search-secondary"
  resource_group_name = azurerm_resource_group.ai.name
  location            = "eastus2"
  sku                 = "standard"
  replica_count       = 2
}

resource "azurerm_traffic_manager_profile" "search" {
  name                   = "tm-search"
  resource_group_name    = azurerm_resource_group.ai.name
  traffic_routing_method = "Priority"
}

resource "azurerm_traffic_manager_endpoint" "primary" {
  name       = "primary"
  profile_id = azurerm_traffic_manager_profile.search.id
  type       = "azureEndpoints"
}

resource "azurerm_cognitive_account" "ai" {
  name                = "resilient-ai"
  resource_group_name = azurerm_resource_group.ai.name
  location            = "uaenorth"
  kind                = "OpenAI"
  sku_name            = "S0"
}

resource "azurerm_cognitive_deployment" "global" {
  name                 = "global"
  cognitive_account_id = azurerm_cognitive_account.ai.id

  scale {
    type     = "GlobalStandard"
    capacity = 1
  }
}

resource "azurerm_monitor_activity_log_alert" "service_health" {
  name                = "service-health"
  resource_group_name = azurerm_resource_group.ai.name
  scopes              = ["/subscriptions/test"]
}
'''

    parsed = TerraformParser().parse_hcl(terraform)
    generator = TerraformResourceGenerator("00000000-0000-0000-0000-000000000000")
    generator.add_resources(parsed)
    resources, edges = generator.generate()

    resource_types = {resource["type"] for resource in resources["resources"]}
    assert len(resources["resources"]) == 8
    assert "microsoft.search/searchservices" in resource_types
    assert "microsoft.network/trafficmanagerprofiles" in resource_types
    assert "microsoft.network/trafficmanagerprofiles/endpoints" in resource_types
    assert "microsoft.cognitiveservices/accounts" in resource_types
    assert "microsoft.cognitiveservices/accounts/deployments" in resource_types
    assert "microsoft.insights/activitylogalerts" in resource_types
    relationships = {edge["relationship"] for edge in edges["edges"]}
    assert "contained_in" in relationships
    assert all(edge["source"] != edge["target"] for edge in edges["edges"])
    cognitive_account = next(
      resource
      for resource in resources["resources"]
      if resource["type"] == "microsoft.cognitiveservices/accounts"
    )
    assert cognitive_account["properties"]["deployments"][0]["scale"][0]["type"] == "GlobalStandard"


def test_parse_directory_resolves_quoted_variable_labels(tmp_path) -> None:
    (tmp_path / "variables.tf").write_text(
        'variable "location" { default = "uaenorth" }', encoding="utf-8"
    )
    (tmp_path / "main.tf").write_text(
        '''
resource "azurerm_search_service" "primary" {
  name                = "search-primary"
  resource_group_name = "rg-ai"
  location            = var.location
  sku                 = "standard"
}
''',
        encoding="utf-8",
    )

    parsed = TerraformParser().parse_directory(tmp_path)
    assert parsed[0].attributes["location"] == "uaenorth"
    assert "__is_block__" not in str(parsed[0].attributes)