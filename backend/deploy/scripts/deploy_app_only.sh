#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="${SCRIPT_DIR}/../vm-terraform"
REPO_DIR="${SCRIPT_DIR}/../../.."

TFVARS_PATH="${1:-${TF_DIR}/terraform.tfvars}"

if [[ ! -f "$TFVARS_PATH" ]]; then
  echo "tfvars not found: $TFVARS_PATH"
  exit 1
fi

for cmd in terraform az rsync ssh scp mktemp awk sha256sum; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing dependency: $cmd"
    exit 1
  fi
done

compute_hash() {
  local target_dir="$1"
  if [[ ! -d "$target_dir" ]]; then
    echo ""
    return
  fi

  (
    cd "$target_dir"
    find . -type f \
      -not -path '*/.venv/*' \
      -not -path '*/node_modules/*' \
      -not -path '*/dist/*' \
      -not -path '*/.pytest_cache/*' \
      -not -path '*/__pycache__/*' \
      -not -path '*/.mypy_cache/*' \
      -not -name '*.pyc' \
      -print0 \
      | sort -z \
      | xargs -0 sha256sum
  ) | sha256sum | awk '{print $1}'
}

echo "==> Resolving VM target from Terraform outputs"
pushd "$TF_DIR" >/dev/null
terraform init -input=false >/dev/null
TF_OUT="$(terraform output -json)"
popd >/dev/null

RESOURCE_GROUP="$(echo "$TF_OUT" | jq -r '.resource_group_name.value')"
VM_NAME="$(echo "$TF_OUT" | jq -r '.vm_name.value')"
VM_USER="$(echo "$TF_OUT" | jq -r '.vm_admin_username.value')"

BACKEND_HASH="$(compute_hash "${REPO_DIR}/backend")"
FRONTEND_HASH="$(compute_hash "${REPO_DIR}/frontend")"

echo "Local backend hash:  ${BACKEND_HASH}"
echo "Local frontend hash: ${FRONTEND_HASH}"

TMP_DIR="$(mktemp -d)"
SSH_CONFIG_FILE="${TMP_DIR}/ssh_config"

echo "==> Generating Entra SSH config"
az ssh config \
  --resource-group "$RESOURCE_GROUP" \
  --name "$VM_NAME" \
  --file "$SSH_CONFIG_FILE" \
  --overwrite >/dev/null

SSH_TARGET="$(awk '/^Host / { print $2; exit }' "$SSH_CONFIG_FILE")"
if [[ -z "$SSH_TARGET" ]]; then
  echo "Failed to resolve SSH host alias from generated config"
  exit 1
fi

echo "==> Ensuring writable app directories on VM"
ssh -F "$SSH_CONFIG_FILE" -o StrictHostKeyChecking=accept-new "$SSH_TARGET" \
  "sudo mkdir -p /opt/azure-resilience-iq/backend /opt/azure-resilience-iq/frontend && \
   sudo chown -R ${VM_USER}:${VM_USER} /opt/azure-resilience-iq"

REMOTE_STATE="$(ssh -F "$SSH_CONFIG_FILE" -o StrictHostKeyChecking=accept-new "$SSH_TARGET" '
  set -e
  STATE_FILE=/opt/azure-resilience-iq/.deploy-hashes.env
  if [[ -f "$STATE_FILE" ]]; then
    cat "$STATE_FILE"
  fi
')"

REMOTE_BACKEND_HASH="$(echo "$REMOTE_STATE" | awk -F= '/^BACKEND_HASH=/{print $2}' | tail -n1)"
REMOTE_FRONTEND_HASH="$(echo "$REMOTE_STATE" | awk -F= '/^FRONTEND_HASH=/{print $2}' | tail -n1)"

DEPLOY_BACKEND=false
DEPLOY_FRONTEND=false

if [[ "$BACKEND_HASH" != "$REMOTE_BACKEND_HASH" ]]; then
  DEPLOY_BACKEND=true
fi

if [[ "$FRONTEND_HASH" != "$REMOTE_FRONTEND_HASH" ]]; then
  DEPLOY_FRONTEND=true
fi

if [[ "$DEPLOY_BACKEND" == "false" && "$DEPLOY_FRONTEND" == "false" ]]; then
  echo "No backend/frontend changes detected since last app deploy."
  rm -rf "$TMP_DIR"
  exit 0
fi

echo "==> Changes detected"
echo "Deploy backend:  $DEPLOY_BACKEND"
echo "Deploy frontend: $DEPLOY_FRONTEND"

if [[ "$DEPLOY_BACKEND" == "true" ]]; then
  echo "==> Syncing backend"
  rsync -az --delete \
    --omit-dir-times \
    --no-perms --no-owner --no-group \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='data' \
    --exclude='aprl/.git' \
    --rsync-path='sudo rsync' \
    -e "ssh -F $SSH_CONFIG_FILE -o StrictHostKeyChecking=accept-new" \
    "${REPO_DIR}/backend/" \
    "${SSH_TARGET}:/opt/azure-resilience-iq/backend/"
fi

if [[ "$DEPLOY_FRONTEND" == "true" ]]; then
  echo "==> Syncing frontend"
  rsync -az --delete \
    --omit-dir-times \
    --no-perms --no-owner --no-group \
    --exclude='.git' \
    --exclude='node_modules' \
    --exclude='dist' \
    --rsync-path='sudo rsync' \
    -e "ssh -F $SSH_CONFIG_FILE -o StrictHostKeyChecking=accept-new" \
    "${REPO_DIR}/frontend/" \
    "${SSH_TARGET}:/opt/azure-resilience-iq/frontend/"
fi

echo "==> Applying remote updates"
ssh -F "$SSH_CONFIG_FILE" -o StrictHostKeyChecking=accept-new "$SSH_TARGET" \
  "VM_USER='${VM_USER}' DEPLOY_BACKEND='${DEPLOY_BACKEND}' DEPLOY_FRONTEND='${DEPLOY_FRONTEND}' BACKEND_HASH='${BACKEND_HASH}' FRONTEND_HASH='${FRONTEND_HASH}' bash -s" <<'EOF'
set -euo pipefail

sudo mkdir -p /opt/azure-resilience-iq
sudo chown -R ${VM_USER}:${VM_USER} /opt/azure-resilience-iq
sudo chown -R ${VM_USER}:${VM_USER} /opt/azure-resilience-iq/backend /opt/azure-resilience-iq/frontend

if [[ "${DEPLOY_BACKEND}" == "true" ]]; then
  echo "--- backend: installing dependencies + restarting service"
  sudo rm -rf /opt/azure-resilience-iq/backend/.venv
  sudo python3 -m venv /opt/azure-resilience-iq/backend/.venv
  sudo /opt/azure-resilience-iq/backend/.venv/bin/pip install -r /opt/azure-resilience-iq/backend/requirements.txt
  sudo systemctl restart azure-resilience-iq-backend
fi

if [[ "${DEPLOY_FRONTEND}" == "true" ]]; then
  echo "--- frontend: building + reloading nginx"
  sudo npm ci --prefix /opt/azure-resilience-iq/frontend
  sudo npm run build --prefix /opt/azure-resilience-iq/frontend
  sudo systemctl reload nginx
fi

cat > /tmp/.deploy-hashes.env <<EOT
BACKEND_HASH=${BACKEND_HASH}
FRONTEND_HASH=${FRONTEND_HASH}
UPDATED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOT

sudo mv /tmp/.deploy-hashes.env /opt/azure-resilience-iq/.deploy-hashes.env
EOF

rm -rf "$TMP_DIR"

echo ""
echo "Incremental app deployment complete."
