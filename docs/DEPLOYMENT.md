# Deployment & Operations

Setup, configuration, and deployment guide for Azure Resiliency IQ. For an overview of what the solution is and how it works, see the [main README](../README.md).

## Prerequisites

### Required Software

- **Python 3.12+** - Required for the backend
- **Node.js 18+** and **npm** - Required for the frontend
- **Azure CLI** - Required for Azure authentication

### Azure Requirements

- Azure subscription with resources to analyze
- Azure AI Foundry project with deployed agents
- Appropriate Azure RBAC permissions to query resources

#### Deploy principal (identity running `terraform apply`)

By default (`assign_rbac_roles = false`, `assign_entra_login_roles = false`) the deployment performs **no RBAC writes**, so **Contributor** at the resource group / subscription is sufficient to provision everything.

| Permission | Scope | When required |
|---|---|---|
| Contributor | Resource group / subscription | Always — creates all resources |
| `Microsoft.Authorization/roleAssignments/write` | Subscription | Only if `assign_rbac_roles = true` |
| `Microsoft.Authorization/roleDefinitions/write` | Subscription | Only if `assign_rbac_roles = true` (custom Service Group Member Writer role) |
| `Microsoft.Authorization/roleAssignments/write` | Management group | Only if `assign_rbac_roles = true` **and** `enable_workload_management_group_rbac = true` |

With the toggles off, a privileged operator (**Owner** or **User Access Administrator**) grants the managed-identity roles **after** deployment by running the emitted `terraform output rbac_grant_commands` (and `terraform output entra_login_grant_commands` for Entra VM sign-in). Set the toggles to `true` to have Terraform assign the roles during apply, which then requires Owner or Contributor + User Access Administrator at deploy time.

## Installation

### 1. Clone the Repository

```bash
git clone <repository-url>
cd azure-resilience-iq
git submodule update --init --recursive
```

The submodule command initializes the Azure Proactive Resiliency Library (APRL) at `backend/aprl`, which provides the resiliency rules used for evaluation.

### 2. Backend Setup

#### Install Python Dependencies

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Notes:
- `pip install -e .` is supported (the backend packages only the `app/` module).
- `data/` contains runtime artifacts (collector output, overrides, annotations) and is intentionally not packaged.

#### Configure Application Settings

Edit `backend/config/app_config.yaml` to set LLM settings:

```yaml
llm:
  enabled: true
  batch_threshold: 50
  max_nodes_per_batch: 30

ai_agent: {}
```

Set `llm.enabled` to `false` to skip LLM calls and omit annotations from responses.

#### Configure Chat Feature (Optional)

The application includes an **AI-powered chat assistant** that helps analyze infrastructure, suggest remediation, and answer questions about your workload through direct Foundry Agents.

**Create Environment File**:

Create a `backend/.env` file (copy from `backend/.env.sample` if available) and add:

For deployed VM runtime, these values are auto-generated into `/etc/azure-resilience-iq.env` by `backend/deploy/scripts/deploy_vm_stack.sh`; local `backend/.env` is for local/dev execution.

```env
# Required Foundry endpoint
AI_FOUNDRY_PROJECT_ENDPOINT=https://<your-foundry-resource>.services.ai.azure.com/api/projects/<project-name>

# Required/expected Foundry + agent settings
AI_FOUNDRY_OPENAI_API_VERSION=2024-10-21
AI_FOUNDRY_REASONING_MODEL=gpt-5.4-mini
AI_FOUNDRY_EMBEDDING_MODEL=text-embedding-3-small
AI_FOUNDRY_CHAT_AGENT_REFERENCE=chat-agent
AI_FOUNDRY_RESILIENCE_AGENT_REFERENCE=resilience-agent
AI_FOUNDRY_ANNOTATIONS_AGENT_REFERENCE=annotations-agent
AI_FOUNDRY_TERRAFORM_AGENT_REFERENCE=terraform-compiler-agent

# Optional data storage mode (default local)
DATA_STORAGE_BACKEND=local
# DATA_STORAGE_ACCOUNT=<storage-account-name>
# DATA_STORAGE_CONTAINER=deployment-state
# DATA_STORAGE_PREFIX=<project/environment-prefix>
# DATA_DIR=./data
```

**Features**:
- Natural language infrastructure analysis
- Resource-specific recommendations
- Remediation guidance with step-by-step instructions
- Cost and performance impact analysis
- Terraform code generation
- Dependency relationship suggestions
- Query-type-specific responses (findings, remediation, terraform, connections)

**Note**: The `.env` file is gitignored and should never be committed to the repository. Each developer needs their own local configuration.

### 3. Frontend Setup

```bash
cd frontend
npm install
```

### 4. Azure CLI Authentication

Before running the collector, authenticate with Azure CLI:

```bash
az login
az account set --subscription <your-subscription-id>
```

## Usage

Data collection, resiliency evaluation, and annotations are all triggered from the web UI — you do not run the collector or analysis modules manually. Start the backend and frontend, then drive everything from the app.

### Step 1: Start the Backend Server

Run the FastAPI backend:

```bash
uvicorn app.main:app --reload --port 8000
```

The backend API will be available at `http://localhost:8000`.

> ⚠️ **Security note:** This backend has **no authentication** and queries Azure
> using your local credentials. Run it locally only. Do **not** bind it to all
> interfaces (`--host 0.0.0.0`) or expose it to untrusted networks. Browser
> origins are restricted via the `CORS_ALLOWED_ORIGINS` environment variable
> (defaults to `http://localhost:5173`); see `backend/.env.sample`.

**API Health Check**:
```bash
curl http://localhost:8000/health
```

### Step 2: Start the Frontend

In a new terminal:

```bash
cd frontend
npm run dev
```

The frontend will be available at `http://localhost:5173`.

### Step 3: Collect and Analyze from the UI

When you first open the app, you'll see a subscription selector in the sidebar. You can:
- Select a **single subscription** to view its workload graph
- Select **multiple subscriptions** to view a merged cross-subscription graph
- **Import a Terraform configuration** for pre-deployment analysis (no Azure access required)
- Create and save **workload views** with specific filter configurations

Selecting a subscription (or importing Terraform) triggers resource collection, dependency detection, resiliency evaluation, and — when Foundry/LLM is configured — annotations. Results are organized by subscription ID under `data/`; set a different base directory via `data.dir` in `backend/config/app_config.yaml`.

## Development Workflow

Run the backend and frontend in separate terminals; collect, evaluate, and refresh data from the UI.

1. **Terminal 1 - Backend**:
   ```bash
   cd backend
   source .venv/bin/activate
   uvicorn app.main:app --reload --port 8000
   ```

2. **Terminal 2 - Frontend**:
   ```bash
   cd frontend
   npm run dev
   ```

### Refresh Data

To update the graph with new Azure resources, re-select the subscription (or click "Reload") in the UI — collection, evaluation, and annotations re-run automatically.

## Automated Azure VM Deployment (Terraform + Foundry + Search)

Production-style infrastructure deployment and application packaging are driven by `backend/deploy/scripts/deploy_vm_stack.sh`.

### What the stack provisions

- **Compute/Network**: Private Windows Server 2022 VM, VNet/subnets, NSG, Azure Bastion Standard enabled by default with an explicit opt-out, and private endpoints.
- **Azure AI Foundry (new model)**:
  - `azurerm_cognitive_account` (`AIServices`)
  - `azurerm_cognitive_account_project`
  - Reasoning deployment (`gpt-5.4-mini` by default)
  - Embedding deployment (`text-embedding-3-small`) required for hydration.
- **Azure AI Search** with private networking.
- **RBAC** for VM managed identity (Foundry, OpenAI inference, Search service/index operations).

#### VM Managed Identity — roles required at runtime

By default these are **not** assigned by Terraform (Contributor-only deployment). A privileged operator assigns them after deployment via `terraform output rbac_grant_commands`, or you set `assign_rbac_roles = true` to have Terraform assign them during apply.

| Role | Scope |
|---|---|
| Foundry User | AI Foundry hub |
| Cognitive Services OpenAI User | AI Foundry hub |
| Foundry User | AI Foundry project |
| Search Service Contributor | Azure AI Search |
| Search Index Data Contributor | Azure AI Search |
| Storage Blob Data Contributor | Storage account |
| Reader | Current subscription |
| Reader | Management group — only if `enable_workload_management_group_rbac = true` |
| Custom: Service Group Member Writer (`serviceGroupMember/write/read/delete`) | Subscription or management group |

**Optional / requires Global Admin (post-deploy):** grant `Service Group Reader` at the tenant-root service group scope to allow the app to read/import Service Groups created by other principals. The `service_group_root_reader_grant_command` Terraform output provides the exact `az` command.

### End-to-end deployment command

```bash
cd backend/deploy/scripts
bash deploy_vm_stack.sh ../vm-terraform/terraform.tfvars
```

Use `--agents-migrate` when you need to force agent/tool reconciliation:

```bash
bash deploy_vm_stack.sh ../vm-terraform/terraform.tfvars --agents-migrate
```

### Provisioning flow (automated)

1. Terraform apply for infra + Foundry + model deployments.
2. Create/verify Foundry project Azure AI Search connection (`azure-ai-search-default`).
3. Build an application ZIP.
4. Transfer and bootstrap automatically through Bastion, or create a local handoff bundle for operator-managed private transfer.
5. Ensure Search indexes:
  - `learn-aprl-index`
  - `learn-terraform-index`
6. Ensure Foundry agents and attach tools:
  - `chat-agent` → Search (`learn-aprl-index`) + Microsoft Learn MCP
  - `resilience-agent` → Search (`learn-aprl-index`) + Microsoft Learn MCP
  - `terraform-compiler-agent` → Search (`learn-terraform-index`)
  - `annotations-agent` → no tools
7. Refresh RAG data into `backend/agent/rag` (staged swap on success).
8. Hydrate indexes (embeddings + upload):
  - APRL corpus from `backend/aprl/docs` and `backend/aprl/azure-resources` (`.md/.txt/.rst/.yaml/.yml/.kql`).
  - Terraform corpus from `backend/agent/rag` only.
9. Build the frontend and install FastAPI as a Windows service bound to `127.0.0.1:80`.

Azure Bastion Standard is enabled by default for automatic private package transfer and RDP access. Bastion receives a public IP and incurs ongoing charges, but the VM has no public IP and its RDP/SSH rules accept traffic only from `AzureBastionSubnet`. Use `--disable-bastion` when an approved private connection already exists; the script then creates `/tmp/azure-resilience-iq-handoff-<vm-name>` for manual transfer and finalization. Blob Storage stays private in both modes.

### Incremental updates (no Terraform re-provision)

Use app-only deployment for backend/frontend code changes:

```bash
cd backend/deploy/scripts
./deploy_app_only.sh ../vm-terraform/terraform.tfvars
```

What it does:
- Packages the current backend/frontend source.
- Transfers automatically through the existing Bastion by default, or creates a manual handoff bundle with `--disable-bastion`.
- Reinstalls backend dependencies, rebuilds the frontend, and restarts the Windows service.
- Skips Terraform apply, Foundry agent reconciliation, RAG refresh, and index hydration.

### Safe teardown

Audit the resource group before destroying the stack, then run the approved teardown:

```bash
cd backend/deploy/scripts
bash safe_destroy_vm_stack.sh ../vm-terraform/terraform.tfvars --audit-only
bash safe_destroy_vm_stack.sh ../vm-terraform/terraform.tfvars --auto-approve
```

The wrapper blocks unexpected unmanaged resources, removes only verified generated network remnants, and retries transient Foundry project concurrency failures.

### Model capacity / quota

- Embedding deployment capacity is Terraform-managed via `embedding_model_capacity`.
- For this environment, `350` was validated as the usable max and should be set in `backend/deploy/vm-terraform/terraform.tfvars`.
- Dynamic "use all available quota" is not always deterministically available from current account usage APIs.
