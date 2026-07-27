#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="${SCRIPT_DIR}/../vm-terraform"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <terraform.tfvars> [--auto-approve] [--force]"
  exit 1
fi

TFVARS_PATH=""
AUTO_APPROVE=false
FORCE=false

for arg in "$@"; do
  case "$arg" in
    --auto-approve)
      AUTO_APPROVE=true
      ;;
    --force)
      FORCE=true
      ;;
    -* )
      echo "Unknown option: $arg"
      echo "Usage: $0 <terraform.tfvars> [--auto-approve] [--force]"
      exit 1
      ;;
    *)
      if [[ -z "$TFVARS_PATH" ]]; then
        TFVARS_PATH="$arg"
      else
        echo "Unexpected argument: $arg"
        exit 1
      fi
      ;;
  esac
done

if [[ -z "$TFVARS_PATH" || ! -f "$TFVARS_PATH" ]]; then
  echo "tfvars not found: ${TFVARS_PATH:-<empty>}"
  exit 1
fi

for cmd in terraform az python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing dependency: $cmd"
    exit 1
  fi
done

extract_tfvar_value() {
  local key="$1"
  local file="$2"

  local line
  line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$file" | head -n1 || true)"
  if [[ -z "$line" ]]; then
    return 0
  fi

  line="${line%%#*}"
  line="${line#*=}"
  line="$(echo "$line" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"

  if [[ "$line" =~ ^\"(.*)\"$ ]]; then
    echo "${BASH_REMATCH[1]}"
  else
    echo "$line"
  fi
}

pushd "$TF_DIR" >/dev/null
terraform init -input=false >/dev/null

# Resolve RG deterministically for unmanaged-resource audit.
RG_NAME=""
RG_NAME="$(terraform state show azurerm_resource_group.this 2>/dev/null | awk -F'=' '/^[[:space:]]*name[[:space:]]*=/{gsub(/[[:space:]"]/,"",$2); print $2; exit}')"

if [[ -z "$RG_NAME" ]]; then
  RG_FROM_TFVARS="$(extract_tfvar_value "resource_group_name" "$TFVARS_PATH")"
  PROJECT_NAME="$(extract_tfvar_value "project_name" "$TFVARS_PATH")"
  ENVIRONMENT_NAME="$(extract_tfvar_value "environment" "$TFVARS_PATH")"

  PROJECT_NAME="${PROJECT_NAME:-resilienceiq}"
  ENVIRONMENT_NAME="${ENVIRONMENT_NAME:-dev}"

  if [[ -n "${RG_FROM_TFVARS:-}" ]]; then
    RG_NAME="$RG_FROM_TFVARS"
  else
    RG_NAME="rg-${PROJECT_NAME}-${ENVIRONMENT_NAME}"
  fi
fi

RG_NAME="$(echo "$RG_NAME" | head -n1 | tr -d '\r')"

if [[ -n "$RG_NAME" ]] && ! az group exists --name "$RG_NAME" >/dev/null 2>&1; then
  echo "==> Resource group '$RG_NAME' does not exist (nothing to audit)"
  RG_NAME=""
fi

if [[ -n "$RG_NAME" ]]; then
  echo "==> Auditing unmanaged resources in resource group: $RG_NAME"

  # Build managed ID list from Terraform state.
  MANAGED_IDS_RAW=""
  while IFS= read -r addr; do
    id_line="$(terraform state show -no-color "$addr" 2>/dev/null | awk -F' = ' '/^[[:space:]]*id[[:space:]]*=/{print $2; exit}' | tr -d '"' || true)"
    if [[ "$id_line" == /subscriptions/* ]]; then
      MANAGED_IDS_RAW+="$id_line"$'\n'
    fi
  done < <(terraform state list)

  AZ_RESOURCES_RAW="$(az resource list -g "$RG_NAME" --query "[].{id:id,managedBy:managedBy,type:type,name:name}" -o json 2>/dev/null || echo '[]')"

  export MANAGED_IDS_RAW AZ_RESOURCES_RAW FORCE
  AUDIT_RESULT="$(python3 - <<'PY'
import os
import re
import json

managed = {line.strip().lower() for line in os.environ.get("MANAGED_IDS_RAW", "").splitlines() if line.strip()}

try:
    actual = json.loads(os.environ.get("AZ_RESOURCES_RAW", "[]"))
except json.JSONDecodeError:
    actual = []

# Known ephemeral orphan currently seen in this stack:
# vnet-<prefix>-snet-private-endpoints-nsg-<region>
allowed_patterns = [
    re.compile(r".*/providers/microsoft\.network/networksecuritygroups/vnet-.*-snet-private-endpoints-nsg-.*$", re.IGNORECASE),
]

autodelete = []
unexpected = []
derived_managed = []

for item in actual:
    rid = (item.get("id") or "").strip()
    if not rid:
        continue

    rid_l = rid.lower()
    if rid_l in managed:
        continue

    managed_by = (item.get("managedBy") or "").strip().lower()
    if managed_by and managed_by in managed:
        derived_managed.append(rid)
        continue

    if any(p.match(rid) for p in allowed_patterns):
        autodelete.append(rid)
    else:
        unexpected.append(rid)

print("DERIVED_MANAGED_COUNT=" + str(len(derived_managed)))
print("AUTO_DELETE_COUNT=" + str(len(autodelete)))
print("UNEXPECTED_COUNT=" + str(len(unexpected)))
for rid in derived_managed:
    print("DERIVED_MANAGED=" + rid)
for rid in autodelete:
    print("AUTO_DELETE=" + rid)
for rid in unexpected:
    print("UNEXPECTED=" + rid)
PY
)"

  echo "$AUDIT_RESULT"

  AUTO_DELETE_COUNT="$(echo "$AUDIT_RESULT" | awk -F= '/^AUTO_DELETE_COUNT=/{print $2; exit}')"
  UNEXPECTED_COUNT="$(echo "$AUDIT_RESULT" | awk -F= '/^UNEXPECTED_COUNT=/{print $2; exit}')"

  if [[ "${AUTO_DELETE_COUNT:-0}" -gt 0 ]]; then
    echo "==> Removing known unmanaged ephemeral resources"
    while IFS= read -r line; do
      [[ "$line" == AUTO_DELETE=* ]] || continue
      rid="${line#AUTO_DELETE=}"
      echo "Deleting unmanaged known resource: $rid"
      az resource delete --ids "$rid" --only-show-errors >/dev/null
    done <<< "$AUDIT_RESULT"
  fi

  if [[ "${UNEXPECTED_COUNT:-0}" -gt 0 && "$FORCE" != "true" ]]; then
    echo "Unexpected unmanaged resources detected. Refusing destroy to protect user-managed resources."
    echo "Re-run with --force to continue anyway, or remove these resources first:"
    echo "$AUDIT_RESULT" | awk -F= '/^UNEXPECTED=/{print " - " $2}'
    popd >/dev/null
    exit 1
  fi
fi

DESTROY_ARGS=(destroy -var-file="$TFVARS_PATH")
if [[ "$AUTO_APPROVE" == "true" ]]; then
  DESTROY_ARGS+=(-auto-approve)
fi

echo "==> Running terraform ${DESTROY_ARGS[*]}"
terraform "${DESTROY_ARGS[@]}"

popd >/dev/null
