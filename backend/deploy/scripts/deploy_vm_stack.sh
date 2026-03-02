#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="${SCRIPT_DIR}/../vm-terraform"
REPO_DIR="${SCRIPT_DIR}/../../.."

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <terraform.tfvars> [--agents-migrate] [--dry-run]"
  echo "Example: $0 ${TF_DIR}/terraform.tfvars"
  exit 1
fi

AGENTS_MIGRATE=false
DRY_RUN=false
TFVARS_PATH=""

for arg in "$@"; do
  case "$arg" in
    --agents-migrate)
      AGENTS_MIGRATE=true
      ;;
    --dry-run)
      DRY_RUN=true
      ;;
    -*)
      echo "Unknown option: $arg"
      echo "Usage: $0 <terraform.tfvars> [--agents-migrate] [--dry-run]"
      exit 1
      ;;
    *)
      if [[ -z "$TFVARS_PATH" ]]; then
        TFVARS_PATH="$arg"
      else
        echo "Unexpected argument: $arg"
        echo "Usage: $0 <terraform.tfvars> [--agents-migrate] [--dry-run]"
        exit 1
      fi
      ;;
  esac
done

if [[ -z "$TFVARS_PATH" ]]; then
  echo "Missing required argument: <terraform.tfvars>"
  echo "Usage: $0 <terraform.tfvars> [--agents-migrate] [--dry-run]"
  exit 1
fi

for cmd in terraform az jq rsync ssh scp mktemp sha256sum; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing dependency: $cmd"
    exit 1
  fi
done

if [[ ! -f "$TFVARS_PATH" ]]; then
  echo "tfvars not found: $TFVARS_PATH"
  exit 1
fi

ensure_vm_running() {
  local resource_group="$1"
  local vm_name="$2"
  local max_attempts=60
  local sleep_seconds=5

  local current_state
  current_state="$(az vm get-instance-view \
    --resource-group "$resource_group" \
    --name "$vm_name" \
    --query "instanceView.statuses[?starts_with(code, 'PowerState/')].displayStatus | [0]" \
    -o tsv 2>/dev/null || true)"

  if [[ "$current_state" != "VM running" ]]; then
    echo "==> VM is not running (state: ${current_state:-unknown}); starting it"
    az vm start --resource-group "$resource_group" --name "$vm_name" --only-show-errors >/dev/null
  else
    echo "==> VM is already running"
  fi

  for ((attempt=1; attempt<=max_attempts; attempt++)); do
    current_state="$(az vm get-instance-view \
      --resource-group "$resource_group" \
      --name "$vm_name" \
      --query "instanceView.statuses[?starts_with(code, 'PowerState/')].displayStatus | [0]" \
      -o tsv 2>/dev/null || true)"

    if [[ "$current_state" == "VM running" ]]; then
      echo "✓ VM is running"
      return 0
    fi

    echo "Waiting for VM to reach running state (attempt ${attempt}/${max_attempts}, current: ${current_state:-unknown})"
    sleep "$sleep_seconds"
  done

  echo "VM did not reach running state in time"
  return 1
}

compute_file_hash() {
  local file_path="$1"
  if [[ -f "$file_path" ]]; then
    sha256sum "$file_path" | awk '{print $1}'
  else
    echo ""
  fi
}

compute_paths_hash() {
  {
    for path in "$@"; do
      if [[ -f "$path" ]]; then
        sha256sum "$path"
      elif [[ -d "$path" ]]; then
        (
          cd "$path"
          find . -type f -print0 | sort -z | xargs -0 sha256sum
        )
      fi
    done
  } | sha256sum | awk '{print $1}'
}

if [[ "$DRY_RUN" == "true" ]]; then
  echo "==> Dry run mode (no changes will be applied)"
  echo "Terraform dir: $TF_DIR"
  echo "Repo dir: $REPO_DIR"
  echo "tfvars: $TFVARS_PATH"
  echo "agents migrate: $AGENTS_MIGRATE"
  echo
  echo "Would run:"
  echo "  terraform -chdir=$TF_DIR init"
  echo "  terraform -chdir=$TF_DIR apply -var-file=$TFVARS_PATH -auto-approve"
  echo "  package repository and upload to VM"
  echo "  install backend/frontend dependencies on VM"
  echo "  run provision_foundry_assets.py from VM private network"
  echo "  configure systemd + nginx and restart services"
  echo
  echo "Dry run complete"
  exit 0
fi

echo "==> Provisioning Azure infrastructure"
pushd "$TF_DIR" >/dev/null
terraform init
terraform apply -var-file="$TFVARS_PATH" -auto-approve
TF_OUT="$(terraform output -json)"
popd >/dev/null

RESOURCE_GROUP="$(echo "$TF_OUT" | jq -r '.resource_group_name.value')"
VM_NAME="$(echo "$TF_OUT" | jq -r '.vm_name.value')"
VM_USER="$(echo "$TF_OUT" | jq -r '.vm_admin_username.value')"
VM_IP="$(echo "$TF_OUT" | jq -r '.vm_public_ip.value')"
SEARCH_ENDPOINT="$(echo "$TF_OUT" | jq -r '.search_endpoint.value')"
FOUNDRY_PROJECT_ENDPOINT="$(echo "$TF_OUT" | jq -r '.foundry_project_endpoint.value')"
FOUNDRY_ACCOUNT_NAME="$(echo "$TF_OUT" | jq -r '.foundry_hub_name.value')"
FOUNDRY_PROJECT_NAME="$(echo "$TF_OUT" | jq -r '.foundry_project_name.value')"
TF_REASONING_MODEL="$(echo "$TF_OUT" | jq -r '.reasoning_model_deployment_name.value // empty')"
TF_EMBEDDING_MODEL="$(echo "$TF_OUT" | jq -r '.embedding_model_deployment_name.value // empty')"
STORAGE_ACCOUNT_NAME="$(echo "$TF_OUT" | jq -r '.storage_account_name.value // empty')"
DEPLOYMENT_STATE_CONTAINER_NAME="$(echo "$TF_OUT" | jq -r '.deployment_state_container_name.value // empty')"
DEPLOYMENT_STATE_PREFIX="$(echo "$TF_OUT" | jq -r '.deployment_state_prefix.value // empty')"

ENV_FILE="${REPO_DIR}/backend/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

REASONING_MODEL="${AI_FOUNDRY_REASONING_MODEL:-${TF_REASONING_MODEL:-gpt-4.1}}"
EMBEDDING_MODEL="${AI_FOUNDRY_EMBEDDING_MODEL:-${TF_EMBEDDING_MODEL:-text-embedding-3-small}}"
OPENAI_API_VERSION="${AI_FOUNDRY_OPENAI_API_VERSION:-2025-03-01-preview}"
EFFECTIVE_FOUNDRY_PROJECT_ENDPOINT="${FOUNDRY_PROJECT_ENDPOINT}"
if [[ -n "${AI_FOUNDRY_PROJECT_ENDPOINT_OVERRIDE:-}" ]]; then
  EFFECTIVE_FOUNDRY_PROJECT_ENDPOINT="${AI_FOUNDRY_PROJECT_ENDPOINT_OVERRIDE}"
  echo "⚠ Overriding Terraform Foundry endpoint using AI_FOUNDRY_PROJECT_ENDPOINT_OVERRIDE"
fi
SEARCH_CONNECTION_NAME="${AI_FOUNDRY_SEARCH_CONNECTION_NAME:-azure-ai-search-default}"
RECREATE_ARG=""
if [[ "${AGENTS_MIGRATE}" == "true" ]]; then
  RECREATE_ARG="--recreate-existing"
fi

BACKEND_REQUIREMENTS_HASH="$(compute_file_hash "${REPO_DIR}/backend/requirements.txt")"
FRONTEND_DEPS_HASH="$(compute_paths_hash "${REPO_DIR}/frontend/package.json" "${REPO_DIR}/frontend/package-lock.json")"
RAG_INPUT_HASH="$(compute_paths_hash \
  "${REPO_DIR}/backend/aprl/docs" \
  "${REPO_DIR}/backend/aprl/azure-resources" \
  "${REPO_DIR}/backend/agent/rag/terraform/terraform_urls.txt" \
  "${REPO_DIR}/backend/app/llm/ingest_search.py" \
  "${REPO_DIR}/backend/app/tools/bootstrap_rag_sources.py")"

echo "Using Foundry project endpoint: ${EFFECTIVE_FOUNDRY_PROJECT_ENDPOINT}"
echo "Using reasoning deployment: ${REASONING_MODEL}"
echo "Using embedding deployment: ${EMBEDDING_MODEL}"
if [[ -n "${STORAGE_ACCOUNT_NAME}" && -n "${DEPLOYMENT_STATE_CONTAINER_NAME}" ]]; then
  echo "Using deployment state blob container: ${STORAGE_ACCOUNT_NAME}/${DEPLOYMENT_STATE_CONTAINER_NAME}"
fi

echo "==> Ensuring Foundry project Azure AI Search connection"
SEARCH_CONNECTION_FILE="$(mktemp)"
cat > "$SEARCH_CONNECTION_FILE" <<EOT
{
  "type": "CognitiveSearch",
  "target": "${SEARCH_ENDPOINT}",
  "metadata": {
    "managedBy": "deploy_vm_stack.sh",
    "purpose": "agent-search"
  }
}
EOT

if az cognitiveservices account project connection show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FOUNDRY_ACCOUNT_NAME" \
  --project-name "$FOUNDRY_PROJECT_NAME" \
  --connection-name "$SEARCH_CONNECTION_NAME" \
  --only-show-errors >/dev/null 2>&1; then
  echo "✓ Found existing project connection: ${SEARCH_CONNECTION_NAME}"
else
  az cognitiveservices account project connection create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$FOUNDRY_ACCOUNT_NAME" \
    --project-name "$FOUNDRY_PROJECT_NAME" \
    --connection-name "$SEARCH_CONNECTION_NAME" \
    --file "$SEARCH_CONNECTION_FILE" \
    --only-show-errors >/dev/null
  echo "✓ Created project connection: ${SEARCH_CONNECTION_NAME}"
fi
rm -f "$SEARCH_CONNECTION_FILE"

echo "==> Packaging application"
TMP_DIR="$(mktemp -d)"
ARCHIVE_PATH="${TMP_DIR}/app.tar.gz"
SSH_CONFIG_FILE="${TMP_DIR}/ssh_config"
KNOWN_HOSTS_FILE="${TMP_DIR}/known_hosts"
touch "${KNOWN_HOSTS_FILE}"
tar \
  --exclude='.git' \
  --exclude='.github' \
  --exclude='.gitignore' \
  --exclude='.gitmodules' \
  --exclude='.vscode' \
  --exclude='.venv' \
  --exclude='ai-context' \
  --exclude='frontend/node_modules' \
  --exclude='backend/.env' \
  --exclude='backend/.venv' \
  --exclude='backend/data' \
  --exclude='backend/sample' \
  --exclude='backend/tools' \
  -czf "$ARCHIVE_PATH" \
  -C "$REPO_DIR" .

echo "==> Ensuring az ssh extension"
az extension add --name ssh --upgrade --only-show-errors >/dev/null

echo "==> Generating Entra SSH config"
ensure_vm_running "$RESOURCE_GROUP" "$VM_NAME"
az ssh config \
  --resource-group "$RESOURCE_GROUP" \
  --name "$VM_NAME" \
  --file "$SSH_CONFIG_FILE" \
  --overwrite >/dev/null

echo "==> Uploading and deploying application on VM via Entra SSH"
SSH_TARGET="$(awk '/^Host / { print $2; exit }' "$SSH_CONFIG_FILE")"
if [[ -z "$SSH_TARGET" ]]; then
  echo "Failed to resolve SSH host alias from generated config: $SSH_CONFIG_FILE"
  exit 1
fi

scp \
  -F "$SSH_CONFIG_FILE" \
  -o UserKnownHostsFile="${KNOWN_HOSTS_FILE}" \
  -o StrictHostKeyChecking=accept-new \
  "$ARCHIVE_PATH" "${SSH_TARGET}:/tmp/azure-resilience-iq.tar.gz"

ssh \
  -F "$SSH_CONFIG_FILE" \
  -o UserKnownHostsFile="${KNOWN_HOSTS_FILE}" \
  -o StrictHostKeyChecking=accept-new \
  "$SSH_TARGET" \
  "VM_USER='${VM_USER}' FOUNDRY_PROJECT_ENDPOINT='${EFFECTIVE_FOUNDRY_PROJECT_ENDPOINT}' REASONING_MODEL='${REASONING_MODEL}' SEARCH_ENDPOINT='${SEARCH_ENDPOINT}' RECREATE_ARG='${RECREATE_ARG}' OPENAI_API_VERSION='${OPENAI_API_VERSION}' EMBEDDING_MODEL='${EMBEDDING_MODEL}' BACKEND_REQUIREMENTS_HASH='${BACKEND_REQUIREMENTS_HASH}' FRONTEND_DEPS_HASH='${FRONTEND_DEPS_HASH}' RAG_INPUT_HASH='${RAG_INPUT_HASH}' STORAGE_ACCOUNT_NAME='${STORAGE_ACCOUNT_NAME}' DEPLOYMENT_STATE_CONTAINER_NAME='${DEPLOYMENT_STATE_CONTAINER_NAME}' DEPLOYMENT_STATE_PREFIX='${DEPLOYMENT_STATE_PREFIX}' bash -s" <<'EOF'
set -euo pipefail

sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip nginx curl ca-certificates gnupg

if ! command -v node >/dev/null 2>&1 || [[ "$(node -v | sed 's/^v//' | cut -d. -f1)" -lt 20 ]]; then
  sudo dpkg --configure -a || true
  sudo apt-get -f install -y || true
  sudo apt-get remove -y nodejs npm libnode-dev nodejs-doc || true
  sudo apt-get autoremove -y || true

  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get update
  sudo apt-get install -y nodejs
fi

if ! command -v node >/dev/null 2>&1 || [[ "$(node -v | sed 's/^v//' | cut -d. -f1)" -lt 20 ]]; then
  echo "Node.js 20+ is required, but found: $(node -v 2>/dev/null || echo 'not installed')"
  exit 1
fi

sudo mkdir -p /opt/azure-resilience-iq
sudo chown -R ${VM_USER}:${VM_USER} /opt/azure-resilience-iq
PERSIST_TMP_DIR="/tmp/azure-resilience-iq-persist"
sudo rm -rf "${PERSIST_TMP_DIR}"
sudo mkdir -p "${PERSIST_TMP_DIR}"

if [[ -d /opt/azure-resilience-iq/backend/data ]]; then
  sudo mv /opt/azure-resilience-iq/backend/data "${PERSIST_TMP_DIR}/backend-data"
fi

if [[ -d /opt/azure-resilience-iq/backend/.venv ]]; then
  sudo mv /opt/azure-resilience-iq/backend/.venv "${PERSIST_TMP_DIR}/backend-venv"
fi

if [[ -d /opt/azure-resilience-iq/backend/agent/rag ]]; then
  sudo mv /opt/azure-resilience-iq/backend/agent/rag "${PERSIST_TMP_DIR}/backend-rag"
fi

if [[ -f /opt/azure-resilience-iq/.deploy-state.env ]]; then
  sudo mv /opt/azure-resilience-iq/.deploy-state.env "${PERSIST_TMP_DIR}/deploy-state.env"
fi

sudo rm -rf /opt/azure-resilience-iq/*
sudo tar -xzf /tmp/azure-resilience-iq.tar.gz -C /opt/azure-resilience-iq

sudo mkdir -p /opt/azure-resilience-iq/backend
if [[ -d "${PERSIST_TMP_DIR}/backend-data" ]]; then
  sudo mv "${PERSIST_TMP_DIR}/backend-data" /opt/azure-resilience-iq/backend/data
else
  sudo mkdir -p /opt/azure-resilience-iq/backend/data
fi

if [[ -d "${PERSIST_TMP_DIR}/backend-venv" ]]; then
  sudo mv "${PERSIST_TMP_DIR}/backend-venv" /opt/azure-resilience-iq/backend/.venv
fi

if [[ -d "${PERSIST_TMP_DIR}/backend-rag" ]]; then
  sudo mkdir -p /opt/azure-resilience-iq/backend/agent
  sudo mv "${PERSIST_TMP_DIR}/backend-rag" /opt/azure-resilience-iq/backend/agent/rag
fi

if [[ -f "${PERSIST_TMP_DIR}/deploy-state.env" ]]; then
  sudo mv "${PERSIST_TMP_DIR}/deploy-state.env" /opt/azure-resilience-iq/.deploy-state.env
fi

sudo rm -rf "${PERSIST_TMP_DIR}"
sudo chown -R ${VM_USER}:${VM_USER} /opt/azure-resilience-iq

sudo -u ${VM_USER} \
  FOUNDRY_PROJECT_ENDPOINT="${FOUNDRY_PROJECT_ENDPOINT}" \
  OPENAI_API_VERSION="${OPENAI_API_VERSION}" \
  REASONING_MODEL="${REASONING_MODEL}" \
  EMBEDDING_MODEL="${EMBEDDING_MODEL}" \
  SEARCH_ENDPOINT="${SEARCH_ENDPOINT}" \
  BACKEND_REQUIREMENTS_HASH="${BACKEND_REQUIREMENTS_HASH}" \
  FRONTEND_DEPS_HASH="${FRONTEND_DEPS_HASH}" \
  RAG_INPUT_HASH="${RAG_INPUT_HASH}" \
  STORAGE_ACCOUNT_NAME="${STORAGE_ACCOUNT_NAME}" \
  DEPLOYMENT_STATE_CONTAINER_NAME="${DEPLOYMENT_STATE_CONTAINER_NAME}" \
  DEPLOYMENT_STATE_PREFIX="${DEPLOYMENT_STATE_PREFIX}" \
  RECREATE_ARG="${RECREATE_ARG}" \
  bash <<'EOS'
set -euo pipefail

DEPLOY_STATE_FILE="/opt/azure-resilience-iq/.deploy-state.env"
DATA_DIR="/opt/azure-resilience-iq/backend/data"
BLOB_STATE_TMP="/tmp/azure-resilience-iq-deploy-state.env"
BOOTSTRAP_VENV_DIR="/tmp/azure-resilience-iq-bootstrap-venv"
export DEPLOY_STATE_FILE DATA_DIR BLOB_STATE_TMP
STORAGE_ENABLED=false
if [[ -n "${STORAGE_ACCOUNT_NAME:-}" && -n "${DEPLOYMENT_STATE_CONTAINER_NAME:-}" ]]; then
  STORAGE_ENABLED=true
fi

cd /opt/azure-resilience-iq/backend

if [[ ! -f deploy/scripts/provision_foundry_assets.py ]]; then
  echo "Missing provisioning script at /opt/azure-resilience-iq/backend/deploy/scripts/provision_foundry_assets.py"
  find /opt/azure-resilience-iq -maxdepth 5 -name provision_foundry_assets.py 2>/dev/null || true
  exit 1
fi

if [[ "${STORAGE_ENABLED}" == "true" ]]; then
  echo "==> Restoring deployment state from Blob (if available)"
  rm -rf "${BOOTSTRAP_VENV_DIR}"
  python3 -m venv "${BOOTSTRAP_VENV_DIR}"
  source "${BOOTSTRAP_VENV_DIR}/bin/activate"
  pip install --quiet azure-identity azure-storage-blob

  if ! python - <<'PY'; then
import os
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceNotFoundError

storage_account = os.environ.get("STORAGE_ACCOUNT_NAME", "").strip()
container_name = os.environ.get("DEPLOYMENT_STATE_CONTAINER_NAME", "").strip()
prefix = os.environ.get("DEPLOYMENT_STATE_PREFIX", "").strip().strip("/")
state_target = os.environ.get("BLOB_STATE_TMP", "").strip()

if not storage_account or not container_name:
    raise SystemExit(0)

state_blob = f"{prefix}/deploy-state.env" if prefix else "deploy-state.env"

service = BlobServiceClient(
    account_url=f"https://{storage_account}.blob.core.windows.net",
    credential=DefaultAzureCredential(),
)
container = service.get_container_client(container_name)

try:
  payload = container.download_blob(state_blob).readall()
except ResourceNotFoundError:
  raise SystemExit(0)

os.makedirs(os.path.dirname(state_target), exist_ok=True)
with open(state_target, "wb") as handle:
  handle.write(payload)
print(f"✓ Restored deploy-state from blob: {state_blob}")
PY
    echo "⚠ Blob restore failed; continuing with local state only"
  fi
  deactivate || true
fi

if [[ -f "${BLOB_STATE_TMP}" ]]; then
  cp "${BLOB_STATE_TMP}" "${DEPLOY_STATE_FILE}"
fi

if [[ -f "${DEPLOY_STATE_FILE}" ]]; then
  source "${DEPLOY_STATE_FILE}"
fi

PREV_BACKEND_REQUIREMENTS_HASH="${BACKEND_REQUIREMENTS_HASH_STATE:-}"
PREV_FRONTEND_DEPS_HASH="${FRONTEND_DEPS_HASH_STATE:-}"
PREV_RAG_INPUT_HASH="${RAG_INPUT_HASH_STATE:-}"
PREV_SEARCH_ENDPOINT="${SEARCH_ENDPOINT_STATE:-}"
PREV_EMBEDDING_MODEL="${EMBEDDING_MODEL_STATE:-}"
PREV_FOUNDRY_PROJECT_ENDPOINT="${FOUNDRY_PROJECT_ENDPOINT_STATE:-}"

SKIP_BACKEND_DEPS=false
if [[ -x .venv/bin/python && "${BACKEND_REQUIREMENTS_HASH}" == "${PREV_BACKEND_REQUIREMENTS_HASH}" ]]; then
  SKIP_BACKEND_DEPS=true
fi

if [[ "${SKIP_BACKEND_DEPS}" == "true" ]]; then
  echo "==> Backend dependency installation unchanged; skipping pip install"
else
  rm -rf .venv
  python3 -m venv .venv
  if [[ ! -x .venv/bin/python ]]; then
    echo "Backend virtualenv python binary not found after venv creation"
    exit 1
  fi

  source .venv/bin/activate
  pip install -r requirements.txt
fi

source .venv/bin/activate

export PATH="/usr/bin:/usr/local/bin:/bin:${PATH}"
hash -r

if ! command -v node >/dev/null 2>&1; then
  echo "Node.js not found in PATH during frontend build phase"
  exit 1
fi

NODE_MAJOR="$(node -v | sed 's/^v//' | cut -d. -f1)"
if [[ "${NODE_MAJOR}" -lt 20 ]]; then
  echo "Node.js 20+ required for frontend build, found: $(node -v)"
  exit 1
fi

python -V
node -v

echo "==> Provisioning Search indexes and Foundry agents from VM private network"
PROVISION_EXIT=1
for attempt in 1 2 3 4 5 6; do
  python -u \
    deploy/scripts/provision_foundry_assets.py \
    --foundry-project-endpoint "${FOUNDRY_PROJECT_ENDPOINT}" \
    --reasoning-model "${REASONING_MODEL}" \
    --search-endpoint "${SEARCH_ENDPOINT}" \
    ${RECREATE_ARG} \
    2>&1 | tee /tmp/azure-resilience-iq-provision.log
  PROVISION_EXIT="${PIPESTATUS[0]}"
  if [[ "${PROVISION_EXIT}" -eq 0 ]]; then
    break
  fi

  if grep -qiE "PermissionDenied|does not have permissions|agents/write|agents/read|AIServices/connections/read" /tmp/azure-resilience-iq-provision.log; then
    if [[ "${attempt}" -lt 6 ]]; then
      SLEEP_SECONDS="$((attempt * 30))"
      echo "Provisioning attempt ${attempt} failed due to RBAC/permission propagation. Retrying in ${SLEEP_SECONDS}s..."
      sleep "${SLEEP_SECONDS}"
      continue
    fi
  fi

  break
done

if [[ "${PROVISION_EXIT}" -ne 0 ]]; then
  echo "Foundry/Search provisioning failed (exit ${PROVISION_EXIT}). Log: /tmp/azure-resilience-iq-provision.log"
  exit "${PROVISION_EXIT}"
fi
echo "Foundry/Search provisioning completed successfully."

echo "==> Verifying embedding model accessibility for hydration"
EMBEDDING_MODEL_RESOLVED="$(FOUNDRY_PROJECT_ENDPOINT="${FOUNDRY_PROJECT_ENDPOINT}" OPENAI_API_VERSION="${OPENAI_API_VERSION}" EMBEDDING_MODEL="${EMBEDDING_MODEL}" python - <<'PY'
import os
import sys
from urllib.parse import urlparse
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "").strip()
api_version = os.environ.get("OPENAI_API_VERSION", "2025-03-01-preview").strip() or "2025-03-01-preview"
primary = os.environ.get("EMBEDDING_MODEL", "").strip()

def _derive_azure_openai_endpoint(project_endpoint: str) -> str:
  parsed = urlparse(project_endpoint)
  host = (parsed.hostname or "").strip().lower()
  suffix = ".services.ai.azure.com"
  if not host.endswith(suffix):
    return ""
  subdomain = host[:-len(suffix)]
  if not subdomain:
    return ""
  return f"https://{subdomain}.openai.azure.com"

candidates = []
for candidate in [
  primary,
  "text-embedding-3-small",
  "text-embedding-ada-002",
]:
  candidate = (candidate or "").strip()
  if candidate and candidate not in candidates:
    candidates.append(candidate)

if not endpoint:
  print("ERROR: missing FOUNDRY_PROJECT_ENDPOINT", file=sys.stderr)
  sys.exit(1)

credential = DefaultAzureCredential()
azure_endpoint = _derive_azure_openai_endpoint(endpoint)
azure_client = None
if azure_endpoint:
  try:
    token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
    azure_client = AzureOpenAI(
      azure_endpoint=azure_endpoint,
      api_version=api_version,
      azure_ad_token_provider=token_provider,
    )
  except Exception:
    azure_client = None

with AIProjectClient(endpoint=endpoint, credential=credential) as project_client:
  try:
    openai_client = project_client.get_openai_client(api_version=api_version)
  except TypeError:
    openai_client = project_client.get_openai_client()
  last_error = None
  project_embeddings_supported = True
  for model_name in candidates:
    if project_embeddings_supported:
      try:
        openai_client.embeddings.create(
          model=model_name,
          input=["health-check"],
        )
        print(model_name)
        sys.exit(0)
      except Exception as exc:
        last_error = exc
        error_text = str(exc).lower()
        if "404" in error_text or "not found" in error_text:
          project_embeddings_supported = False

    if azure_client is not None:
      try:
        azure_client.embeddings.create(
          model=model_name,
          input=["health-check"],
        )
        print(model_name)
        sys.exit(0)
      except Exception as fallback_exc:
        last_error = fallback_exc
        continue

print("ERROR: no accessible embedding model found among candidates: " + ",".join(candidates), file=sys.stderr)
if last_error is not None:
  print("Last probe error: " + str(last_error), file=sys.stderr)
sys.exit(2)
PY
)"

if [[ -z "${EMBEDDING_MODEL_RESOLVED}" ]]; then
  echo "Embedding model probe failed: no accessible embedding model could be resolved."
  exit 1
fi

if [[ "${EMBEDDING_MODEL_RESOLVED}" != "${EMBEDDING_MODEL}" ]]; then
  echo "⚠ Requested embedding model '${EMBEDDING_MODEL}' is not accessible; using '${EMBEDDING_MODEL_RESOLVED}' for hydration."
fi
EMBEDDING_MODEL="${EMBEDDING_MODEL_RESOLVED}"

RAG_BASE_DIR="/opt/azure-resilience-iq/backend/agent/rag"
RAG_STAGE_DIR="/tmp/azure-resilience-iq-rag-refresh"
RAG_TERRAFORM_DOCS="${RAG_BASE_DIR}/terraform/docs"
RAG_TERRAFORM_URLS="${RAG_BASE_DIR}/terraform/terraform_urls.txt"

SKIP_RAG_HYDRATION=false
if [[ "${RAG_INPUT_HASH}" == "${PREV_RAG_INPUT_HASH}" \
   && "${SEARCH_ENDPOINT}" == "${PREV_SEARCH_ENDPOINT}" \
   && "${EMBEDDING_MODEL}" == "${PREV_EMBEDDING_MODEL}" \
   && "${FOUNDRY_PROJECT_ENDPOINT}" == "${PREV_FOUNDRY_PROJECT_ENDPOINT}" ]]; then
  SKIP_RAG_HYDRATION=true
fi

if [[ "${SKIP_RAG_HYDRATION}" == "true" ]]; then
  echo "==> RAG inputs unchanged; skipping RAG refresh and index hydration"
else
  echo "==> Refreshing RAG data (AVM/CAF)"
  if python -u -m app.tools.bootstrap_rag_sources \
    --repo-root /opt/azure-resilience-iq \
    --base-rag-dir "${RAG_STAGE_DIR}" \
    --refresh-clone \
    2>&1 | tee /tmp/azure-resilience-iq-rag-refresh.log; then
    rm -rf "${RAG_BASE_DIR}"
    mkdir -p "$(dirname "${RAG_BASE_DIR}")"
    mv "${RAG_STAGE_DIR}" "${RAG_BASE_DIR}"
    echo "✓ RAG data refresh completed"
  else
    rm -rf "${RAG_STAGE_DIR}"
    echo "⚠ RAG refresh failed; using existing local RAG folder content"
  fi

  mkdir -p "${RAG_TERRAFORM_DOCS}"
  TERRAFORM_LOCAL_PATH="${RAG_TERRAFORM_DOCS}"

  INGEST_ARGS=(
    --targets aprl,terraform
    --include-glob '**/*'
    --aprl-local-path /opt/azure-resilience-iq/backend/aprl/docs
    --aprl-local-path /opt/azure-resilience-iq/backend/aprl/azure-resources
    --terraform-local-path "${TERRAFORM_LOCAL_PATH}"
    --aprl-index-name learn-aprl-index
    --terraform-index-name learn-terraform-index
    --search-endpoint "${SEARCH_ENDPOINT}"
    --embedding-model "${EMBEDDING_MODEL}"
  )

  if [[ -f "${RAG_TERRAFORM_URLS}" ]]; then
    INGEST_ARGS+=(--terraform-url-file "${RAG_TERRAFORM_URLS}")
  fi

  echo "==> Hydrating Azure AI Search indexes"
  AI_FOUNDRY_PROJECT_ENDPOINT="${FOUNDRY_PROJECT_ENDPOINT}" AI_FOUNDRY_OPENAI_API_VERSION="${OPENAI_API_VERSION}" python -u -m app.llm.ingest_search \
    "${INGEST_ARGS[@]}" \
    2>&1 | tee /tmp/azure-resilience-iq-hydration.log
  HYDRATION_EXIT="${PIPESTATUS[0]}"
  if [[ "${HYDRATION_EXIT}" -ne 0 ]]; then
    echo "Index hydration failed (exit ${HYDRATION_EXIT}). Log: /tmp/azure-resilience-iq-hydration.log"
    exit "${HYDRATION_EXIT}"
  fi
  echo "Index hydration completed successfully."
fi

cd /opt/azure-resilience-iq/frontend
if [[ -d node_modules && "${FRONTEND_DEPS_HASH}" == "${PREV_FRONTEND_DEPS_HASH}" ]]; then
  echo "==> Frontend dependencies unchanged; skipping npm ci"
else
  npm ci
fi
npm run build

cat > "${DEPLOY_STATE_FILE}" <<EOT
BACKEND_REQUIREMENTS_HASH_STATE=${BACKEND_REQUIREMENTS_HASH}
FRONTEND_DEPS_HASH_STATE=${FRONTEND_DEPS_HASH}
RAG_INPUT_HASH_STATE=${RAG_INPUT_HASH}
SEARCH_ENDPOINT_STATE=${SEARCH_ENDPOINT}
EMBEDDING_MODEL_STATE=${EMBEDDING_MODEL}
FOUNDRY_PROJECT_ENDPOINT_STATE=${FOUNDRY_PROJECT_ENDPOINT}
UPDATED_AT_STATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOT

if [[ "${STORAGE_ENABLED}" == "true" ]]; then
  echo "==> Syncing deployment state to Blob"

  rm -rf "${BOOTSTRAP_VENV_DIR}"
  python3 -m venv "${BOOTSTRAP_VENV_DIR}"
  source "${BOOTSTRAP_VENV_DIR}/bin/activate"
  pip install --quiet azure-identity azure-storage-blob

  if ! python - <<'PY'; then
import os
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceExistsError

storage_account = os.environ.get("STORAGE_ACCOUNT_NAME", "").strip()
container_name = os.environ.get("DEPLOYMENT_STATE_CONTAINER_NAME", "").strip()
prefix = os.environ.get("DEPLOYMENT_STATE_PREFIX", "").strip().strip("/")
state_source = os.environ.get("DEPLOY_STATE_FILE", "").strip()

if not storage_account or not container_name:
  raise SystemExit(0)

state_blob = f"{prefix}/deploy-state.env" if prefix else "deploy-state.env"

service = BlobServiceClient(
  account_url=f"https://{storage_account}.blob.core.windows.net",
  credential=DefaultAzureCredential(),
)
container = service.get_container_client(container_name)
try:
  container.create_container()
except ResourceExistsError:
  pass

with open(state_source, "rb") as state_handle:
  container.upload_blob(name=state_blob, data=state_handle, overwrite=True)

print(f"✓ Uploaded deploy-state to blob prefix '{prefix}'")
PY
  echo "⚠ Blob sync failed; deployment completed with local state only"
  fi
  deactivate || true
fi
EOS

DATA_STORAGE_BACKEND_VALUE="local"
if [[ -n "${STORAGE_ACCOUNT_NAME:-}" && -n "${DEPLOYMENT_STATE_CONTAINER_NAME:-}" ]]; then
  DATA_STORAGE_BACKEND_VALUE="blob"
fi

sudo tee /etc/azure-resilience-iq.env > /dev/null <<EOT
AI_FOUNDRY_PROJECT_ENDPOINT=${FOUNDRY_PROJECT_ENDPOINT}
AI_FOUNDRY_OPENAI_API_VERSION=${OPENAI_API_VERSION}
AI_FOUNDRY_REASONING_MODEL=${REASONING_MODEL}
AI_FOUNDRY_EMBEDDING_MODEL=${EMBEDDING_MODEL}
AI_FOUNDRY_CHAT_AGENT_REFERENCE=chat-agent
AI_FOUNDRY_RESILIENCE_AGENT_REFERENCE=resilience-agent
AI_FOUNDRY_ANNOTATIONS_AGENT_REFERENCE=annotations-agent
AI_FOUNDRY_TERRAFORM_AGENT_REFERENCE=terraform-compiler-agent
AZURE_SEARCH_ENDPOINT=${SEARCH_ENDPOINT}
AZURE_SEARCH_INDEX_NAME_APRL=learn-aprl-index
AZURE_SEARCH_INDEX_NAME_TERRAFORM=learn-terraform-index
DATA_STORAGE_BACKEND=${DATA_STORAGE_BACKEND_VALUE}
DATA_STORAGE_ACCOUNT=${STORAGE_ACCOUNT_NAME}
DATA_STORAGE_CONTAINER=${DEPLOYMENT_STATE_CONTAINER_NAME}
DATA_STORAGE_PREFIX=${DEPLOYMENT_STATE_PREFIX}
EOT

sudo tee /etc/systemd/system/azure-resilience-iq-backend.service > /dev/null <<EOT
[Unit]
Description=Azure Workload Graph Backend
After=network.target

[Service]
Type=simple
User=${VM_USER}
WorkingDirectory=/opt/azure-resilience-iq/backend
EnvironmentFile=/etc/azure-resilience-iq.env
ExecStart=/opt/azure-resilience-iq/backend/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOT

sudo tee /etc/nginx/sites-available/azure-resilience-iq > /dev/null <<'EOT'
server {
    listen 80;
    server_name _;

    root /opt/azure-resilience-iq/frontend/dist;
    index index.html;

  location /assets/ {
    try_files $uri =404;
    access_log off;
    expires 1y;
    add_header Cache-Control "public, max-age=31536000, immutable";
  }

  location = /index.html {
    try_files /index.html =404;
    add_header Cache-Control "no-cache";
  }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /health {
        proxy_pass http://127.0.0.1:8000/health;
    }

    location = /favicon.ico {
      try_files /favicon.ico =204;
      access_log off;
      log_not_found off;
    }

    location = / {
      try_files /index.html =404;
    }

    location / {
      try_files $uri $uri/ /index.html;
    }
}
EOT

if [[ -d /etc/nginx/sites-enabled ]]; then
  sudo ln -sf \
    /etc/nginx/sites-available/azure-resilience-iq \
    /etc/nginx/sites-enabled/azure-resilience-iq
  sudo rm -f /etc/nginx/sites-enabled/default || true
else
  echo "⚠ /etc/nginx/sites-enabled not found; skipping default-site symlink management"
fi

sudo systemctl daemon-reload
sudo systemctl enable azure-resilience-iq-backend
sudo systemctl restart azure-resilience-iq-backend
sudo systemctl enable nginx
sudo systemctl restart nginx
EOF

rm -rf "$TMP_DIR"

echo
echo "Deployment complete"
echo "VM: ${VM_NAME} (${VM_IP})"
echo "Frontend: http://${VM_IP}"
echo "Backend health: http://${VM_IP}/health"
echo "Foundry endpoint (terraform): ${FOUNDRY_PROJECT_ENDPOINT}"
