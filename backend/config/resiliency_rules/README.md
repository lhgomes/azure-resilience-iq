# Custom Resiliency Rules

This directory contains custom KQL queries for resilience checks that are **pending contribution to APRL** (Azure Proactive Resiliency Library v2).

## Purpose

These queries fill gaps in APRL's current zone redundancy coverage and will be contributed upstream once tested and validated in production.

## Current Custom Rules

### 1. Storage Account Zone Redundancy (`storage-account-zone-redundancy.kql`)

**APRL Gap**: APRL currently has NO zone redundancy check for Storage Accounts despite having 10+ other storage-related checks.

**What it checks**: 
- Identifies Storage Accounts in zone-enabled regions
- Detects accounts using LRS/GRS/RAGRS instead of ZRS/GZRS/RAGZRS
- Provides specific SKU migration recommendations

**Recommendation ID**: `storage-zone-redundancy-001`

**Expected in APRL**: 
- Directory: `azure-resources/Storage/storageAccounts/kql/`
- GUID format: Will be assigned by APRL maintainers upon PR merge

**Contribution Status**: 🟡 Ready for APRL PR

---

### 2. Key Vault Zone Redundancy (`keyvault-zone-redundancy.kql`)

**APRL Gap**: APRL currently has NO zone redundancy check for Key Vault.

**What it checks**:
- Identifies Key Vaults in zone-enabled regions
- Detects vaults NOT using Premium SKU (which includes zone redundancy)
- Recommends Premium upgrade for zone redundancy + HSM-backed keys

**Recommendation ID**: `keyvault-zone-redundancy-001`

**Expected in APRL**:
- Directory: `azure-resources/KeyVault/vaults/kql/`
- GUID format: Will be assigned by APRL maintainers upon PR merge

**Contribution Status**: 🟡 Ready for APRL PR

---

### 3. Azure Synapse Zone Redundancy (`synapse-zone-redundancy.kql`)

**APRL Gap**: APRL currently has NO zone redundancy check for Synapse workspaces.

**What it checks**:
- Identifies Synapse workspaces in zone-enabled regions
- Detects workspaces without availability zone configuration
- Recommends enabling zone redundancy for workspace deployment

**Recommendation ID**: `synapse-zone-redundancy-001`

**Expected in APRL**:
- Directory: `azure-resources/Synapse/workspaces/kql/`
- GUID format: Will be assigned by APRL maintainers upon PR merge

**Contribution Status**: 🟡 Ready for APRL PR

---

### 4. Data Factory Zone Redundancy (`datafactory-zone-redundancy.kql`)

**APRL Gap**: APRL currently has NO zone redundancy check for Data Factory.

**What it checks**:
- Identifies Data Factory instances in zone-enabled regions
- Detects factories without zone configuration
- Recommends deploying in zone-enabled regions for automatic zone redundancy

**Recommendation ID**: `datafactory-zone-redundancy-001`

**Expected in APRL**:
- Directory: `azure-resources/DataFactory/factories/kql/`
- GUID format: Will be assigned by APRL maintainers upon PR merge

**Contribution Status**: 🟡 Ready for APRL PR

---

### 5. Container Instances Zone Redundancy (`containerinstance-zone-redundancy.kql`)

**APRL Gap**: APRL currently has NO zone redundancy check for Container Instances.

**What it checks**:
- Identifies Container Instance groups in zone-enabled regions
- Detects container groups without availability zone deployment
- Recommends deploying across availability zones

**Recommendation ID**: `containerinstance-zone-redundancy-001`

**Expected in APRL**:
- Directory: `azure-resources/ContainerInstance/containerGroups/kql/`
- GUID format: Will be assigned by APRL maintainers upon PR merge

**Contribution Status**: 🟡 Ready for APRL PR

---

## Zone-Enabled Regions (38 total)

All queries filter by the following zone-capable Azure regions:

```
australiaeast, brazilsouth, canadacentral, centralindia, centralus,
eastasia, eastus, eastus2, francecentral, germanywestcentral,
israelcentral, italynorth, japaneast, japanwest, koreacentral,
mexicocentral, newzealandnorth, northeurope, norwayeast, polandcentral,
qatarcentral, southafricanorth, southcentralus, southeastasia,
spaincentral, swedencentral, switzerlandnorth, uaenorth, uksouth,
westeurope, westus2, westus3, usgovvirginia, chinanorth3
```

*(This list matches APRL's existing zone queries)*

---

## How to Test Locally

Run queries against your Azure Resource Graph:

```bash
# Test Storage Account query
az graph query -q "$(cat storage-account-zone-redundancy.kql)"

# Test Key Vault query
az graph query -q "$(cat keyvault-zone-redundancy.kql)"
```

---

## APRL Contribution Workflow

### Step 1: Validate Query Structure

Ensure queries follow APRL patterns:
- ✅ Comment describing purpose
- ✅ Region filtering with zone-enabled regions list
- ✅ Clear `where` conditions for compliance check
- ✅ `project` with recommendationId, name, id, tags, param1, param2, param3

### Step 2: Generate APRL GUID

Before submitting PR to APRL:
```bash
# Generate new GUID for recommendation
uuidgen | tr '[:upper:]' '[:lower:]'
```

Replace temporary `storage-zone-redundancy-001` with generated GUID.

### Step 3: Create APRL PR Structure

For each query, create:
```
azure-resources/Storage/storageAccounts/kql/{GUID}.kql
azure-resources/KeyVault/vaults/kql/{GUID}.kql
```

Update corresponding `recommendations.yaml` files with:
- Recommendation metadata
- Category: HighAvailability
- Impact: High
- Guidance on enabling zone redundancy

### Step 4: Submit PR to APRL

Repository: https://github.com/Azure/Azure-Proactive-Resiliency-Library-v2

PR Title: `Add zone redundancy checks for Storage Accounts and Key Vault`

PR Description:
```markdown
## Summary
Adds zone redundancy detection for:
- Storage Accounts (ZRS/GZRS/RAGZRS)
- Key Vault (Premium SKU with zone redundancy)

## Motivation
Current APRL coverage includes zone checks for VMs, Disks, SQL MI, Event Hub, but
is missing critical storage and security services.

## Testing
Validated against production subscriptions with 100+ storage accounts and key vaults
across multiple regions.

## References
- Azure Storage redundancy: https://learn.microsoft.com/azure/storage/common/storage-redundancy
- Key Vault availability: https://learn.microsoft.com/azure/key-vault/general/disaster-recovery-guidance
```

---

## Integration with Resiliency Analyzer

These queries are automatically loaded by the resilience evaluation system:

```python
# backend/app/resilience/run.py
def load_custom_rules():
    rules_dir = Path("config/resiliency_rules")
    for kql_file in rules_dir.glob("*.kql"):
        # Load and execute custom KQL queries
        ...
```

Results appear in:
- `resilience_evaluations.json` (summary)
- `resilience_evaluations_detailed.json` (full KQL + results)
- `zonal_resilience.json` (zone compliance integration)

---

## Maintenance

Once queries are merged into APRL:

1. **Remove local copies** from this directory
2. **Update APRL submodule** to pull official versions
3. **Archive documentation** of what was contributed

**Tracking Issue**: Create GitHub issue to track APRL PR status

---

## Future Contributions

Other potential APRL zone checks (NOT yet implemented):

| Resource Type | Gap | Priority |
|---|---|---|
| Azure Data Factory | No zone check | Medium |
| Azure Synapse | No zone check | Medium |
| Azure Cache for Redis | Has check (5a44bd30) | ✅ Covered |
| App Service Plans | Has check (88cb90c2) | ✅ Covered |
| Cosmos DB | Has check (921631f6) | ✅ Covered |

---

## Contact

For questions about APRL contributions:
- APRL Repo: https://github.com/Azure/Azure-Proactive-Resiliency-Library-v2
- Discussions: https://github.com/Azure/Azure-Proactive-Resiliency-Library-v2/discussions
