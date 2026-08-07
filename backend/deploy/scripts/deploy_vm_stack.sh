#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$(cd "${SCRIPT_DIR}/../vm-terraform" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
BOOTSTRAP_SCRIPT="${SCRIPT_DIR}/bootstrap_windows_vm.ps1"
TRANSFER_SCRIPT="${SCRIPT_DIR}/prepare_windows_transfer.ps1"

# Git Bash (MSYS2) rewrites arguments beginning with '/' into Windows paths,
# corrupting Azure resource IDs such as /subscriptions/.... Disable that
# conversion for CLI calls that pass resource IDs. The variable is ignored on
# Linux/macOS shells, so behavior there is unchanged.
az_id() { MSYS_NO_PATHCONV=1 az "$@"; }

usage() {
  echo "Usage: $0 <terraform.tfvars> [--enable-bastion|--disable-bastion] [--agents-migrate] [--app-only] [--dry-run]"
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

TFVARS_PATH=""
ENABLE_BASTION=true
AGENTS_MIGRATE=false
APP_ONLY=false
DRY_RUN=false

for arg in "$@"; do
  case "$arg" in
    --enable-bastion) ENABLE_BASTION=true ;;
    --disable-bastion) ENABLE_BASTION=false ;;
    --agents-migrate) AGENTS_MIGRATE=true ;;
    --app-only) APP_ONLY=true ;;
    --dry-run) DRY_RUN=true ;;
    -*)
      echo "Unknown option: $arg"
      usage
      exit 1
      ;;
    *)
      if [[ -n "$TFVARS_PATH" ]]; then
        echo "Unexpected argument: $arg"
        usage
        exit 1
      fi
      TFVARS_PATH="$arg"
      ;;
  esac
done

if [[ ! -f "$TFVARS_PATH" ]]; then
  echo "tfvars not found: $TFVARS_PATH"
  exit 1
fi
TFVARS_PATH="$(cd "$(dirname "$TFVARS_PATH")" && pwd)/$(basename "$TFVARS_PATH")"

for script_path in "$BOOTSTRAP_SCRIPT" "$TRANSFER_SCRIPT"; do
  if [[ ! -f "$script_path" ]]; then
    echo "Windows deployment script not found: $script_path"
    exit 1
  fi
done

for command_name in terraform az jq zip mktemp; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing dependency: $command_name"
    exit 1
  fi
done

if [[ "$ENABLE_BASTION" == "true" ]]; then
  for command_name in scp ssh ssh-keygen; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
      echo "Missing dependency required for Bastion transfer: $command_name"
      exit 1
    fi
  done
fi

if [[ "$DRY_RUN" == "true" ]]; then
  echo "Windows VM deployment dry run"
  echo "Terraform: $TF_DIR"
  echo "Variables: $TFVARS_PATH"
  echo "Bastion enabled: $ENABLE_BASTION"
  echo "App only: $APP_ONLY"
  echo "Agents migrate: $AGENTS_MIGRATE"
  if [[ "$ENABLE_BASTION" == "true" ]]; then
    echo "Would validate/apply the private Windows VM infrastructure, transfer the package through Bastion, and invoke the Windows bootstrap through Azure Run Command."
  else
    echo "Would validate/apply the private Windows VM infrastructure and create a local handoff bundle with finalization instructions."
  fi
  exit 0
fi

echo "==> Initializing and validating Terraform"
terraform -chdir="$TF_DIR" init -input=false
terraform -chdir="$TF_DIR" validate

if [[ "$APP_ONLY" != "true" ]]; then
  EXISTING_RESOURCE_GROUP="$(terraform -chdir="$TF_DIR" output -raw resource_group_name 2>/dev/null || true)"
  EXISTING_VM_NAME="$(terraform -chdir="$TF_DIR" output -raw vm_name 2>/dev/null || true)"
  EXISTING_NIC_NAME="$(terraform -chdir="$TF_DIR" output -raw nic_name 2>/dev/null || true)"
  if [[ -z "$EXISTING_NIC_NAME" ]]; then
    EXISTING_NIC_NAME="$(terraform -chdir="$TF_DIR" state show -no-color azurerm_network_interface.this 2>/dev/null | awk '$1 == "name" && $2 == "=" {gsub(/"/, "", $3); print $3; exit}' || true)"
  fi
  if [[ -n "$EXISTING_RESOURCE_GROUP" && -n "$EXISTING_VM_NAME" ]] && \
    az vm show --resource-group "$EXISTING_RESOURCE_GROUP" --name "$EXISTING_VM_NAME" --only-show-errors >/dev/null 2>&1; then
    echo "==> Ensuring the existing VM is running so Terraform can remove extensions"
    az vm start --resource-group "$EXISTING_RESOURCE_GROUP" --name "$EXISTING_VM_NAME" --only-show-errors >/dev/null
  fi

  if [[ -n "$EXISTING_RESOURCE_GROUP" && -n "$EXISTING_NIC_NAME" ]]; then
    EXISTING_PUBLIC_IP_ID="$(az network nic show \
      --resource-group "$EXISTING_RESOURCE_GROUP" \
      --name "$EXISTING_NIC_NAME" \
      --query 'ipConfigurations[0].publicIPAddress.id' \
      --output tsv \
      --only-show-errors 2>/dev/null || true)"
    if [[ -n "$EXISTING_PUBLIC_IP_ID" ]]; then
      echo "==> Detaching the legacy public IP from the private VM network interface"
      az network nic ip-config update \
        --resource-group "$EXISTING_RESOURCE_GROUP" \
        --nic-name "$EXISTING_NIC_NAME" \
        --name ipconfig1 \
        --remove publicIPAddress \
        --only-show-errors >/dev/null
    fi
  fi

  echo "==> Applying private Windows VM infrastructure (Bastion enabled: $ENABLE_BASTION)"
  terraform -chdir="$TF_DIR" apply \
    -var-file="$TFVARS_PATH" \
    -var="enable_bastion=$ENABLE_BASTION" \
    -auto-approve
fi

TF_OUT="$(terraform -chdir="$TF_DIR" output -json)"
RESOURCE_GROUP="$(jq -r '.resource_group_name.value' <<<"$TF_OUT")"
VM_NAME="$(jq -r '.vm_name.value' <<<"$TF_OUT")"
VM_ADMIN_USERNAME="$(jq -r '.vm_admin_username.value' <<<"$TF_OUT")"
BASTION_NAME="$(jq -r '.bastion_name.value // empty' <<<"$TF_OUT")"
SEARCH_ENDPOINT="$(jq -r '.search_endpoint.value' <<<"$TF_OUT")"
FOUNDRY_PROJECT_ENDPOINT="$(jq -r '.foundry_project_endpoint.value' <<<"$TF_OUT")"
FOUNDRY_ACCOUNT_NAME="$(jq -r '.foundry_hub_name.value' <<<"$TF_OUT")"
FOUNDRY_PROJECT_NAME="$(jq -r '.foundry_project_name.value' <<<"$TF_OUT")"
REASONING_MODEL="$(jq -r '.reasoning_model_deployment_name.value' <<<"$TF_OUT")"
EMBEDDING_MODEL="$(jq -r '.embedding_model_deployment_name.value' <<<"$TF_OUT")"
EMBEDDING_ENDPOINT="$(jq -r '.embedding_model_endpoint.value' <<<"$TF_OUT")"
FOUNDRY_ACCOUNT_ID="$(jq -r '.foundry_hub_id.value' <<<"$TF_OUT")"
STORAGE_ACCOUNT_NAME="$(jq -r '.storage_account_name.value' <<<"$TF_OUT")"
CONTAINER_NAME="$(jq -r '.deployment_state_container_name.value' <<<"$TF_OUT")"
DEPLOYMENT_STATE_PREFIX="$(jq -r '.deployment_state_prefix.value' <<<"$TF_OUT")"

if [[ "$ENABLE_BASTION" == "true" && -z "$BASTION_NAME" ]]; then
  echo "Bastion transfer is enabled by default, but Bastion is not deployed. Run a full default deployment first, or use --disable-bastion for a manual handoff." >&2
  exit 1
fi

ENV_FILE="${REPO_DIR}/backend/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

FOUNDRY_PROJECT_ENDPOINT="${AI_FOUNDRY_PROJECT_ENDPOINT_OVERRIDE:-$FOUNDRY_PROJECT_ENDPOINT}"
EMBEDDING_MODEL="${AI_FOUNDRY_EMBEDDING_MODEL:-$EMBEDDING_MODEL}"
OPENAI_API_VERSION="${AI_FOUNDRY_OPENAI_API_VERSION:-2025-03-01-preview}"
SEARCH_CONNECTION_NAME="${AI_FOUNDRY_SEARCH_CONNECTION_NAME:-azure-ai-search-default}"

if [[ "$APP_ONLY" != "true" ]]; then
  echo "==> Approving Azure AI Search private connection to the embedding endpoint"
  while IFS= read -r connection_id; do
    [[ -z "$connection_id" ]] && continue
    az_id network private-endpoint-connection approve \
      --id "$connection_id" \
      --description "Approved for Azure AI Search query vectorization" \
      --only-show-errors >/dev/null
  done < <(az_id network private-endpoint-connection list \
    --id "$FOUNDRY_ACCOUNT_ID" \
    --query "[?properties.privateLinkServiceConnectionState.status=='Pending' && contains(properties.groupIds, 'openai_account')].id" \
    --output tsv \
    --only-show-errors)
fi

if [[ "$APP_ONLY" != "true" ]]; then
  echo "==> Ensuring Foundry project Search connection"
  CONNECTION_FILE="$(mktemp)"
  jq -n \
    --arg target "$SEARCH_ENDPOINT" \
    '{type:"CognitiveSearch",target:$target,metadata:{managedBy:"deploy_vm_stack.sh",purpose:"agent-search"}}' \
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
  rm -f "$CONNECTION_FILE"
fi

echo "==> Packaging application for Windows"
TMP_DIR="$(mktemp -d)"
BASTION_TUNNEL_PID=""
TEMPORARY_SSH_ENABLED=false
remove_temporary_ssh_access() {
  if [[ "$TEMPORARY_SSH_ENABLED" == "true" ]]; then
    echo "==> Removing temporary SSH access"
    az vm run-command invoke \
      --resource-group "$RESOURCE_GROUP" \
      --name "$VM_NAME" \
      --command-id RunPowerShellScript \
      --scripts '$ErrorActionPreference="Continue"; Remove-Item C:\ProgramData\ssh\administrators_authorized_keys -Force -ErrorAction SilentlyContinue; Stop-Service sshd -Force -ErrorAction SilentlyContinue; Set-Service sshd -StartupType Manual -ErrorAction SilentlyContinue' \
      --only-show-errors \
      --output none || true
    TEMPORARY_SSH_ENABLED=false
  fi
}
cleanup() {
  if [[ -n "$BASTION_TUNNEL_PID" ]] && kill -0 "$BASTION_TUNNEL_PID" 2>/dev/null; then
    kill "$BASTION_TUNNEL_PID" 2>/dev/null || true
    wait "$BASTION_TUNNEL_PID" 2>/dev/null || true
  fi
  remove_temporary_ssh_access
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT
ARCHIVE_PATH="${TMP_DIR}/application.zip"
(
  cd "$REPO_DIR"
  zip -qr "$ARCHIVE_PATH" . \
    -x '.git/*' '*/.git/*' '*/.git' '.github/*' '.vscode/*' 'ai-context/*' \
    '.venv/*' 'frontend/node_modules/*' 'frontend/dist/*' \
    'backend/.env' 'backend/.venv/*' 'backend/data/*' 'backend/sample/*' \
    'backend/tools/*' 'backend/deploy/vm-terraform/.terraform/*' \
    'backend/deploy/vm-terraform/terraform.tfstate*' \
    'backend/agent/rag/*/source-repos/*'
)

echo "==> Ensuring Windows VM is running"
az vm start --resource-group "$RESOURCE_GROUP" --name "$VM_NAME" --only-show-errors >/dev/null

if [[ "$ENABLE_BASTION" != "true" ]]; then
  HANDOFF_DIR="${AZURE_RESILIENCE_IQ_HANDOFF_DIR:-/tmp/azure-resilience-iq-handoff-${VM_NAME}}"
  rm -rf "$HANDOFF_DIR"
  mkdir -p "$HANDOFF_DIR"
  cp "$ARCHIVE_PATH" "$HANDOFF_DIR/application.zip"
  cp "$BOOTSTRAP_SCRIPT" "$HANDOFF_DIR/bootstrap_windows_vm.ps1"

  cat >"$HANDOFF_DIR/Finalize-Deployment.ps1" <<EOF
\$ErrorActionPreference = "Stop"
\$bundleRoot = Split-Path -Parent \$MyInvocation.MyCommand.Path
\$isAdministrator = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not \$isAdministrator) {
    throw "Run this script from an elevated PowerShell session."
}

\$bootstrapParameters = @{
  StorageAccountName = '$STORAGE_ACCOUNT_NAME'
  ContainerName = '$CONTAINER_NAME'
  PackageBlobName = 'unused'
  LocalPackagePath = Join-Path \$bundleRoot "application.zip"
  FoundryProjectEndpoint = '$FOUNDRY_PROJECT_ENDPOINT'
  SearchEndpoint = '$SEARCH_ENDPOINT'
  ReasoningModel = '$REASONING_MODEL'
  EmbeddingModel = '$EMBEDDING_MODEL'
  EmbeddingEndpoint = '$EMBEDDING_ENDPOINT'
  OpenAiApiVersion = '$OPENAI_API_VERSION'
  DeploymentStatePrefix = '$DEPLOYMENT_STATE_PREFIX'
  AgentsMigrate = '$AGENTS_MIGRATE'
  AppOnly = '$APP_ONLY'
}
& (Join-Path \$bundleRoot "bootstrap_windows_vm.ps1") @bootstrapParameters
EOF

  echo
  echo "Infrastructure deployment complete; application finalization is delegated to the operator."
  echo "VM: $VM_NAME (private IP only)"
  echo "Handoff bundle: $HANDOFF_DIR"
  echo "After connecting to the VM through your approved private access path:"
  echo "  1. Copy the complete handoff folder to C:\\AzureResilienceIQ-Handoff"
  echo "  2. Open PowerShell as Administrator"
  echo "  3. Run: powershell.exe -ExecutionPolicy Bypass -File C:\\AzureResilienceIQ-Handoff\\Finalize-Deployment.ps1"
  echo "  4. Open http://localhost after the script reports a successful health check"
  exit 0
fi

echo "==> Preparing private package transfer through Azure Bastion"
SSH_KEY_PATH="${TMP_DIR}/id_ed25519"
KNOWN_HOSTS_PATH="${TMP_DIR}/known_hosts"
TUNNEL_LOG_PATH="${TMP_DIR}/bastion-tunnel.log"
LOCAL_TUNNEL_PORT="${BASTION_TUNNEL_PORT:-$((50000 + RANDOM % 1000))}"
ssh-keygen -q -t ed25519 -N '' -f "$SSH_KEY_PATH"
AUTHORIZED_KEY="$(<"${SSH_KEY_PATH}.pub")"

TEMPORARY_SSH_ENABLED=true
az vm run-command invoke \
  --resource-group "$RESOURCE_GROUP" \
  --name "$VM_NAME" \
  --command-id RunPowerShellScript \
  --scripts "@$TRANSFER_SCRIPT" \
  --parameters "AuthorizedKey=$AUTHORIZED_KEY" \
  --query 'value[0].message' \
  --output tsv

VM_RESOURCE_ID="$(az vm show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$VM_NAME" \
  --query id \
  --output tsv \
  --only-show-errors)"
az_id network bastion tunnel \
  --resource-group "$RESOURCE_GROUP" \
  --name "$BASTION_NAME" \
  --target-resource-id "$VM_RESOURCE_ID" \
  --resource-port 22 \
  --port "$LOCAL_TUNNEL_PORT" \
  --only-show-errors >"$TUNNEL_LOG_PATH" 2>&1 &
BASTION_TUNNEL_PID=$!

SSH_COMMON_OPTIONS=(
  -i "$SSH_KEY_PATH"
  -o BatchMode=yes
  -o ConnectTimeout=5
  -o StrictHostKeyChecking=accept-new
  -o "UserKnownHostsFile=$KNOWN_HOSTS_PATH"
)
TRANSFER_READY=false
for attempt in {1..30}; do
  if ssh -p "$LOCAL_TUNNEL_PORT" "${SSH_COMMON_OPTIONS[@]}" "${VM_ADMIN_USERNAME}@127.0.0.1" exit 2>/dev/null; then
    TRANSFER_READY=true
    break
  fi
  if ! kill -0 "$BASTION_TUNNEL_PID" 2>/dev/null; then
    cat "$TUNNEL_LOG_PATH" >&2
    echo "Azure Bastion tunnel exited before SSH became available" >&2
    exit 1
  fi
  sleep 2
done
if [[ "$TRANSFER_READY" != "true" ]]; then
  cat "$TUNNEL_LOG_PATH" >&2
  echo "Timed out waiting for SSH through Azure Bastion" >&2
  exit 1
fi

echo "==> Transferring application package to the Windows VM"
ssh -p "$LOCAL_TUNNEL_PORT" "${SSH_COMMON_OPTIONS[@]}" \
  "${VM_ADMIN_USERNAME}@127.0.0.1" \
  'powershell.exe -NoProfile -Command "New-Item -ItemType Directory -Path C:\AzureResilienceIQ-Deploy -Force | Out-Null"'
scp -P "$LOCAL_TUNNEL_PORT" "${SSH_COMMON_OPTIONS[@]}" \
  "$ARCHIVE_PATH" \
  "${VM_ADMIN_USERNAME}@127.0.0.1:C:/AzureResilienceIQ-Deploy/application.zip"
kill "$BASTION_TUNNEL_PID" 2>/dev/null || true
wait "$BASTION_TUNNEL_PID" 2>/dev/null || true
BASTION_TUNNEL_PID=""
remove_temporary_ssh_access

echo "==> Running Windows bootstrap through Azure VM Run Command"
BOOTSTRAP_STATUS=0
BOOTSTRAP_RESULT="$(az vm run-command invoke \
  --resource-group "$RESOURCE_GROUP" \
  --name "$VM_NAME" \
  --command-id RunPowerShellScript \
  --scripts "@$BOOTSTRAP_SCRIPT" \
  --parameters \
    "StorageAccountName=$STORAGE_ACCOUNT_NAME" \
    "ContainerName=$CONTAINER_NAME" \
    "PackageBlobName=unused" \
    'LocalPackagePath=C:\AzureResilienceIQ-Deploy\application.zip' \
    "FoundryProjectEndpoint=$FOUNDRY_PROJECT_ENDPOINT" \
    "SearchEndpoint=$SEARCH_ENDPOINT" \
    "ReasoningModel=$REASONING_MODEL" \
    "EmbeddingModel=$EMBEDDING_MODEL" \
    "EmbeddingEndpoint=$EMBEDDING_ENDPOINT" \
    "OpenAiApiVersion=$OPENAI_API_VERSION" \
    "DeploymentStatePrefix=$DEPLOYMENT_STATE_PREFIX" \
    "AgentsMigrate=$AGENTS_MIGRATE" \
    "AppOnly=$APP_ONLY" \
  --output json)" || BOOTSTRAP_STATUS=$?
BOOTSTRAP_STDOUT="$(jq -r '[.value[] | select(.code | contains("/StdOut/")) | .message] | join("\n")' <<<"$BOOTSTRAP_RESULT")"
BOOTSTRAP_STDERR="$(jq -r '[.value[] | select(.code | contains("/StdErr/")) | .message] | join("\n")' <<<"$BOOTSTRAP_RESULT")"
printf '%s\n' "$BOOTSTRAP_STDOUT"
if [[ -n "$BOOTSTRAP_STDERR" ]]; then
  printf '%s\n' "$BOOTSTRAP_STDERR" >&2
fi
if [[ "$BOOTSTRAP_STDOUT" != *"Azure Resiliency IQ is available at http://localhost"* ]]; then
  echo "Windows bootstrap did not report a successful localhost health check" >&2
  BOOTSTRAP_STATUS=1
fi

if [[ "$BOOTSTRAP_STATUS" -ne 0 ]]; then
  exit "$BOOTSTRAP_STATUS"
fi

SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
PORTAL_VM_URL="https://portal.azure.com/#resource/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.Compute/virtualMachines/${VM_NAME}/connect"

echo
echo "Deployment complete"
echo "VM: $VM_NAME (private IP only)"
if [[ -n "$BASTION_NAME" ]]; then
  echo "Bastion: $BASTION_NAME"
  echo "Connect: $PORTAL_VM_URL"
  echo "Inside the RDP session, open: http://localhost"
else
  echo "Bastion: disabled"
  echo "Interactive access requires an existing private network path or a full default deployment that enables Bastion."
fi
