#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROVISION_SCRIPT="${SCRIPT_DIR}/provision_foundry_assets.py"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SUBSCRIPTION_ID="$(az account show --query id --output tsv 2>/dev/null || true)"
RESOURCE_GROUP="ResilienceUAE"
FOUNDRY_ACCOUNT_NAME="ResilienceIQ"
FOUNDRY_PROJECT_NAME="resilience-iq-project"
SEARCH_SERVICE_NAME="resilience-iq-ai-search"
SEARCH_CONNECTION_NAME="azure-ai-search-default"
REASONING_MODEL="gpt-5.4-mini"
EMBEDDING_DEPLOYMENT="text-embedding-3-small"
EMBEDDING_MODEL="text-embedding-3-small"
EMBEDDING_MODEL_VERSION="1"
EMBEDDING_CAPACITY="100"
APRL_INDEX_NAME="learn-aprl-hybrid-index"
TERRAFORM_INDEX_NAME="learn-terraform-hybrid-index"
PYTHON_BIN=""
SKIP_INGESTION=false
DRY_RUN=false

usage() {
  cat <<'EOF'
Usage: configure_hybrid_search_dev.sh [options]

Configures existing Azure resources for local vector-semantic hybrid development.
This does not deploy or publish the application.

Options:
  --subscription ID
  --resource-group NAME
  --foundry-account NAME
  --foundry-project NAME
  --search-service NAME
  --search-connection NAME
  --reasoning-model NAME
  --embedding-deployment NAME
  --embedding-model NAME
  --embedding-model-version VERSION
  --embedding-capacity CAPACITY
  --aprl-index NAME
  --terraform-index NAME
  --python PATH
  --skip-ingestion
  --dry-run
  -h, --help
EOF
}

require_value() {
  if [[ $# -lt 2 || -z "$2" ]]; then
    echo "Missing value for $1" >&2
    usage
    exit 1
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --subscription) require_value "$@"; SUBSCRIPTION_ID="$2"; shift 2 ;;
    --resource-group) require_value "$@"; RESOURCE_GROUP="$2"; shift 2 ;;
    --foundry-account) require_value "$@"; FOUNDRY_ACCOUNT_NAME="$2"; shift 2 ;;
    --foundry-project) require_value "$@"; FOUNDRY_PROJECT_NAME="$2"; shift 2 ;;
    --search-service) require_value "$@"; SEARCH_SERVICE_NAME="$2"; shift 2 ;;
    --search-connection) require_value "$@"; SEARCH_CONNECTION_NAME="$2"; shift 2 ;;
    --reasoning-model) require_value "$@"; REASONING_MODEL="$2"; shift 2 ;;
    --embedding-deployment) require_value "$@"; EMBEDDING_DEPLOYMENT="$2"; shift 2 ;;
    --embedding-model) require_value "$@"; EMBEDDING_MODEL="$2"; shift 2 ;;
    --embedding-model-version) require_value "$@"; EMBEDDING_MODEL_VERSION="$2"; shift 2 ;;
    --embedding-capacity) require_value "$@"; EMBEDDING_CAPACITY="$2"; shift 2 ;;
    --aprl-index) require_value "$@"; APRL_INDEX_NAME="$2"; shift 2 ;;
    --terraform-index) require_value "$@"; TERRAFORM_INDEX_NAME="$2"; shift 2 ;;
    --python) require_value "$@"; PYTHON_BIN="$2"; shift 2 ;;
    --skip-ingestion) SKIP_INGESTION=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

for command_name in az jq mktemp; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing dependency: $command_name" >&2
    exit 1
  fi
done

if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "${SCRIPT_DIR}/../../.venv/bin/python" ]]; then
    PYTHON_BIN="${SCRIPT_DIR}/../../.venv/bin/python"
  elif [[ -x "${SCRIPT_DIR}/../../../.venv/bin/python" ]]; then
    PYTHON_BIN="${SCRIPT_DIR}/../../../.venv/bin/python"
  else
    PYTHON_BIN="$(command -v python3 || true)"
  fi
fi

if [[ -z "$SUBSCRIPTION_ID" ]]; then
  echo "No Azure subscription selected. Run 'az login' or pass --subscription." >&2
  exit 1
fi
if [[ ! -f "$PROVISION_SCRIPT" ]]; then
  echo "Provisioning script not found: $PROVISION_SCRIPT" >&2
  exit 1
fi
if [[ "$DRY_RUN" != "true" && ! -x "$PYTHON_BIN" ]]; then
  echo "Python executable not found: $PYTHON_BIN" >&2
  exit 1
fi

SEARCH_RESOURCE_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.Search/searchServices/${SEARCH_SERVICE_NAME}"
FOUNDRY_RESOURCE_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.CognitiveServices/accounts/${FOUNDRY_ACCOUNT_NAME}"
FOUNDRY_PROJECT_RESOURCE_ID="${FOUNDRY_RESOURCE_ID}/projects/${FOUNDRY_PROJECT_NAME}"

echo "Local hybrid semantic Search configuration"
echo "Subscription: $SUBSCRIPTION_ID"
echo "Resource group: $RESOURCE_GROUP"
echo "Foundry account/project: ${FOUNDRY_ACCOUNT_NAME}/${FOUNDRY_PROJECT_NAME}"
echo "Search service: $SEARCH_SERVICE_NAME"
echo "Embedding deployment: $EMBEDDING_DEPLOYMENT"
echo "Hybrid indexes: ${APRL_INDEX_NAME}, ${TERRAFORM_INDEX_NAME}"

if [[ "$DRY_RUN" == "true" ]]; then
  echo "Dry run: would ensure the embedding deployment, managed identities, RBAC, Foundry Search connection, side-by-side hybrid indexes, ingestion, and hybrid agent versions."
  echo "Dry run: no Azure resources or local files were changed."
  exit 0
fi

az account set --subscription "$SUBSCRIPTION_ID"

echo "==> Validating existing Azure resources"
az search service show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$SEARCH_SERVICE_NAME" \
  --only-show-errors >/dev/null
az cognitiveservices account show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FOUNDRY_ACCOUNT_NAME" \
  --only-show-errors >/dev/null

echo "==> Ensuring embedding model deployment"
if ! az cognitiveservices account deployment show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FOUNDRY_ACCOUNT_NAME" \
  --deployment-name "$EMBEDDING_DEPLOYMENT" \
  --only-show-errors >/dev/null 2>&1; then
  az cognitiveservices account deployment create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$FOUNDRY_ACCOUNT_NAME" \
    --deployment-name "$EMBEDDING_DEPLOYMENT" \
    --model-format OpenAI \
    --model-name "$EMBEDDING_MODEL" \
    --model-version "$EMBEDDING_MODEL_VERSION" \
    --sku-name Standard \
    --sku-capacity "$EMBEDDING_CAPACITY" \
    --only-show-errors >/dev/null
fi

echo "==> Ensuring Azure AI Search managed identity"
SEARCH_PRINCIPAL_ID="$(az search service show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$SEARCH_SERVICE_NAME" \
  --query identity.principalId \
  --output tsv \
  --only-show-errors)"
if [[ -z "$SEARCH_PRINCIPAL_ID" ]]; then
  az search service update \
    --resource-group "$RESOURCE_GROUP" \
    --name "$SEARCH_SERVICE_NAME" \
    --identity-type SystemAssigned \
    --only-show-errors >/dev/null
  SEARCH_PRINCIPAL_ID="$(az search service show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$SEARCH_SERVICE_NAME" \
    --query identity.principalId \
    --output tsv \
    --only-show-errors)"
fi

SEARCH_AUTH_OPTIONS="$(az search service show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$SEARCH_SERVICE_NAME" \
  --query 'keys(authOptions)[0]' \
  --output tsv \
  --only-show-errors)"
if [[ "$SEARCH_AUTH_OPTIONS" != "aadOrApiKey" ]]; then
  az rest \
    --method patch \
    --url "https://management.azure.com${SEARCH_RESOURCE_ID}?api-version=2025-05-01" \
    --body '{"properties":{"authOptions":{"aadOrApiKey":{"aadAuthFailureMode":"http403"}}}}' \
    --only-show-errors >/dev/null
fi

FOUNDRY_PROJECT_PRINCIPAL_ID="$(az cognitiveservices account project show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FOUNDRY_ACCOUNT_NAME" \
  --project-name "$FOUNDRY_PROJECT_NAME" \
  --query identity.principalId \
  --output tsv \
  --only-show-errors)"
if [[ -z "$FOUNDRY_PROJECT_PRINCIPAL_ID" ]]; then
  echo "Foundry project must have a system-assigned managed identity." >&2
  exit 1
fi

ensure_principal_role() {
  local principal_id="$1"
  local principal_type="$2"
  local role_name="$3"
  local scope="$4"
  local assignment_count

  assignment_count="$(az role assignment list \
    --assignee "$principal_id" \
    --scope "$scope" \
    --role "$role_name" \
    --query 'length(@)' \
    --output tsv \
    --only-show-errors)"
  if [[ "$assignment_count" == "0" ]]; then
    az role assignment create \
      --assignee-object-id "$principal_id" \
      --assignee-principal-type "$principal_type" \
      --role "$role_name" \
      --scope "$scope" \
      --only-show-errors >/dev/null
  fi
}

echo "==> Ensuring managed identity role assignments"
ensure_principal_role "$SEARCH_PRINCIPAL_ID" ServicePrincipal "Cognitive Services OpenAI User" "$FOUNDRY_RESOURCE_ID"
ensure_principal_role "$FOUNDRY_PROJECT_PRINCIPAL_ID" ServicePrincipal "Search Service Contributor" "$SEARCH_RESOURCE_ID"
ensure_principal_role "$FOUNDRY_PROJECT_PRINCIPAL_ID" ServicePrincipal "Search Index Data Contributor" "$SEARCH_RESOURCE_ID"

echo "==> Ensuring local developer data-plane role assignments"
CURRENT_USER_PRINCIPAL_ID="$(az ad signed-in-user show --query id --output tsv --only-show-errors)"
ensure_principal_role "$CURRENT_USER_PRINCIPAL_ID" User "Search Service Contributor" "$SEARCH_RESOURCE_ID"
ensure_principal_role "$CURRENT_USER_PRINCIPAL_ID" User "Search Index Data Contributor" "$SEARCH_RESOURCE_ID"
ensure_principal_role "$CURRENT_USER_PRINCIPAL_ID" User "Azure AI Developer" "$FOUNDRY_PROJECT_RESOURCE_ID"

SEARCH_ENDPOINT="https://${SEARCH_SERVICE_NAME}.search.windows.net"
FOUNDRY_PROJECT_ENDPOINT="$(az cognitiveservices account project show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FOUNDRY_ACCOUNT_NAME" \
  --project-name "$FOUNDRY_PROJECT_NAME" \
  --query 'properties.endpoints."AI Foundry API"' \
  --output tsv \
  --only-show-errors)"
FOUNDRY_CUSTOM_SUBDOMAIN="$(az cognitiveservices account show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FOUNDRY_ACCOUNT_NAME" \
  --query properties.customSubDomainName \
  --output tsv \
  --only-show-errors)"
EMBEDDING_ENDPOINT="https://${FOUNDRY_CUSTOM_SUBDOMAIN}.openai.azure.com"

echo "==> Ensuring Foundry project Search connection"
CONNECTION_FILE="$(mktemp)"
trap 'rm -f "$CONNECTION_FILE"' EXIT
jq -n \
  --arg target "$SEARCH_ENDPOINT" \
  '{type:"CognitiveSearch",target:$target,metadata:{managedBy:"configure_hybrid_search_dev.sh",purpose:"agent-search"}}' \
  >"$CONNECTION_FILE"
if ! az cognitiveservices account project connection show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FOUNDRY_ACCOUNT_NAME" \
  --project-name "$FOUNDRY_PROJECT_NAME" \
  --connection-name "$SEARCH_CONNECTION_NAME" \
  --only-show-errors >/dev/null 2>&1; then
  az cognitiveservices account project connection create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$FOUNDRY_ACCOUNT_NAME" \
    --project-name "$FOUNDRY_PROJECT_NAME" \
    --connection-name "$SEARCH_CONNECTION_NAME" \
    --file "$CONNECTION_FILE" \
    --only-show-errors >/dev/null
fi

echo "==> Provisioning hybrid Search indexes and Foundry agent versions locally"
"$PYTHON_BIN" "$PROVISION_SCRIPT" \
  --foundry-project-endpoint "$FOUNDRY_PROJECT_ENDPOINT" \
  --reasoning-model "$REASONING_MODEL" \
  --search-endpoint "$SEARCH_ENDPOINT" \
  --aprl-index-name "$APRL_INDEX_NAME" \
  --terraform-index-name "$TERRAFORM_INDEX_NAME" \
  --embedding-endpoint "$EMBEDDING_ENDPOINT" \
  --embedding-deployment "$EMBEDDING_DEPLOYMENT" \
  --embedding-model "$EMBEDDING_MODEL" \
  --recreate-existing

if [[ "$SKIP_INGESTION" != "true" ]]; then
  echo "==> Hydrating side-by-side hybrid indexes from local RAG sources"
  INGEST_ARGS=(
    -m app.llm.ingest_search
    --targets aprl,terraform
    --include-glob "**/*"
    --aprl-local-path "${BACKEND_DIR}/aprl/docs"
    --aprl-local-path "${BACKEND_DIR}/aprl/azure-resources"
    --terraform-local-path "${BACKEND_DIR}/agent/rag/terraform/docs"
    --aprl-index-name "$APRL_INDEX_NAME"
    --terraform-index-name "$TERRAFORM_INDEX_NAME"
    --search-endpoint "$SEARCH_ENDPOINT"
    --embedding-model "$EMBEDDING_MODEL"
  )
  if [[ -f "${BACKEND_DIR}/agent/rag/terraform/terraform_urls.txt" ]]; then
    INGEST_ARGS+=(--terraform-url-file "${BACKEND_DIR}/agent/rag/terraform/terraform_urls.txt")
  fi

  pushd "$BACKEND_DIR" >/dev/null
  PYTHONPATH="$BACKEND_DIR" \
    AI_FOUNDRY_PROJECT_ENDPOINT="$FOUNDRY_PROJECT_ENDPOINT" \
    AI_FOUNDRY_OPENAI_API_VERSION="2025-03-01-preview" \
    AI_FOUNDRY_EMBEDDING_MODEL="$EMBEDDING_MODEL" \
    "$PYTHON_BIN" "${INGEST_ARGS[@]}"
  popd >/dev/null
fi

echo "Local Azure hybrid semantic configuration completed. No application was published."