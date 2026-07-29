#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TFVARS_PATH="${SCRIPT_DIR}/../vm-terraform/terraform.tfvars"

if [[ $# -gt 0 && "$1" != -* ]]; then
  TFVARS_PATH="$1"
  shift
fi

exec "${SCRIPT_DIR}/deploy_vm_stack.sh" "$TFVARS_PATH" --app-only "$@"
