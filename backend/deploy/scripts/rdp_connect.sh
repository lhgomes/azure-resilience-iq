#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="${SCRIPT_DIR}/../vm-terraform"

# Git Bash (MSYS2) rewrites arguments beginning with '/' into Windows paths,
# corrupting Azure resource IDs such as /subscriptions/.... Disable that
# conversion for CLI calls that pass resource IDs. The variable is ignored on
# Linux/macOS shells, so behavior there is unchanged.
az_id() { MSYS_NO_PATHCONV=1 az "$@"; }

TF_OUT="$(terraform -chdir="$TF_DIR" output -json)"
RESOURCE_GROUP="$(jq -r '.resource_group_name.value' <<<"$TF_OUT")"
VM_NAME="$(jq -r '.vm_name.value' <<<"$TF_OUT")"
BASTION_NAME="$(jq -r '.bastion_name.value // empty' <<<"$TF_OUT")"
if [[ -z "$BASTION_NAME" ]]; then
  echo "Azure Bastion is not deployed. Run a full default deployment before using this helper." >&2
  exit 1
fi

VM_ID="$(az vm show --resource-group "$RESOURCE_GROUP" --name "$VM_NAME" --query id -o tsv)"

az_id network bastion rdp \
  --name "$BASTION_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --target-resource-id "$VM_ID" \
  --enable-mfa