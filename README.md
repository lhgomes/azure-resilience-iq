# Azure Resiliency IQ

A full-stack application for visualizing and analyzing Azure workloads using Azure Resource Graph and LLM-powered annotations.

## Prerequisites

### Required Software

- **Python 3.12+** - Required for the backend
- **Node.js 18+** and **npm** - Required for the frontend
- **Azure CLI** - Required for Azure authentication

### Azure Requirements

- Azure subscription with resources to analyze
- Azure AI Foundry project with deployed agents
- Appropriate Azure RBAC permissions to query resources

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

### Step 1: Collect Azure Resources

#### Option A: Azure Resource Graph (Live Resources)

Run the collector to fetch resources from your Azure subscription:

```bash
cd backend
source .venv/bin/activate  # Activate virtual environment if not already active
python -m app.collector.run --subscription-id <your-subscription-id>
```

**Optional Parameters**:
- Filter by resource group: `--resource-group <rg-name>` (can be repeated)
- Filter by tags: `--tag key=value` (can be repeated)

**Example**:
```bash
python -m app.collector.run \
  --subscription-id 12345678-1234-1234-1234-123456789abc \
  --resource-group my-rg \
  --tag environment=production
```

#### Option B: Terraform Configuration (Pre-deployment Analysis)

Import resources directly from Terraform files without Azure access:

```bash
cd backend
source .venv/bin/activate
python -m app.terraform.run --terraform-dir <path-to-terraform-files>
```

**Or via Web UI**:
1. Start the backend server (see Step 4)
2. Open the frontend (see Step 5)
3. Click "Import Terraform Configuration"
4. Upload your `.tf` or `.json` files

Both options create:
- `data/{subscription-id}/resources.json` - Collected Azure resources with subscription metadata
- `data/{subscription-id}/edges.json` - Multi-source dependency edges with signal details

Data is organized by subscription ID. To use a different base directory, set `data.dir` in `backend/config/app_config.yaml`.

### Step 2: Run Resiliency Evaluations

Evaluate resources against Azure Proactive Resiliency Library (APRL) recommendations:

```bash
python -m app.resilience.run --subscription-id <your-subscription-id>
```

This analyzes resources and generates:
- Resiliency recommendations per resource
- Category-based evaluations (Availability, Data, Disaster Recovery, etc.)
- Pass/fail status for each recommendation
- Resiliency scores and weighted metrics
- **Availability zone analysis** (deployment patterns, 3-AZ compliance)
- **Resiliency correlation groups** (Availability Sets, VMSS, Load Balancers, etc.)

Results are saved to `data/{subscription-id}/resilience_evaluations.json`.

### Step 3: Run LLM Annotations (Optional)

If Foundry is configured and `llm.enabled: true`, run the LLM annotator:

```bash
python -m app.llm.run --subscription-id <your-subscription-id>
```

This analyzes the collected resources and generates:
- Display name suggestions
- Layer classifications (L1: core , L2: network/platform, L3: implementation details)
- Criticality scores (1-10) and criticality weights (% distribution)
- Architecture improvement suggestions

Results are saved to `data/{subscription-id}/llm_annotations.json`.

### Step 4: Start the Backend Server

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

### Step 5: Start the Frontend

In a new terminal:

```bash
cd frontend
npm run dev
```

The frontend will be available at `http://localhost:5173`.

**First Time**: When you first open the app, you'll see a subscription selector in the sidebar. You can:
- Select a **single subscription** to view its workload graph
- Select **multiple subscriptions** to view a merged cross-subscription graph
- Create and save **workload views** with specific filter configurations

## Multi-Subscription View & Workload Management

### Multi-Subscription Support

The application supports analyzing **multiple Azure subscriptions simultaneously**:

**Features**:
- Select one or more subscriptions from the sidebar
- View merged graph combining resources from all selected subscriptions
- Unified resilience evaluations across subscriptions
- Cross-subscription filtering (resource groups, services)
- Subscription-aware node and edge metadata

**How It Works**:
1. Collect data from each subscription separately:
   ```bash
   python -m app.collector.run --subscription-id <subscription-1>
   python -m app.resilience.run --subscription-id <subscription-1>
   
   python -m app.collector.run --subscription-id <subscription-2>
   python -m app.resilience.run --subscription-id <subscription-2>
   ```

2. In the UI, select multiple subscriptions from the sidebar
3. The graph automatically merges:
   - Nodes (deduplicated by resource ID)
   - Edges (preserved from all subscriptions)
   - LLM annotations (first occurrence wins)
   - Resiliency evaluations (merged by resource ID)
   - User overrides (combined across subscriptions)

**Use Cases**:
- Cross-subscription dependency analysis
- Enterprise-wide resilience posture
- Multi-tenant workload visualization
- Development/staging/production comparison

### Sidebar Filters

The left sidebar provides multiple **filter layers** to focus your analysis:

**Filter Types**:

| Filter | Purpose | Impact |
|--------|---------|--------|
| **Resource Groups** | Filter resources by Azure resource group | Excludes non-matching resources from graph and scores |
| **Services** | Filter by Azure service type (Compute, Storage, Database, etc.) | Focuses analysis on specific service categories |
| **Validation Sources** | Filter by recommendation source (APRL, Heuristic, LLM, ZoneRecommendation) | Recalculates scores using only selected sources |

**Behavior**:
- Filters are **cumulative** - all active filters must match for a resource to display
- Unchecking all items in a filter **excludes everything** (intentional for temporary exclusions)
- Click "Reset Filters" to restore all filters to checked state
- Filters automatically repopulate when you switch subscriptions (unless you've manually changed them)
- Scores and stats **recalculate in real-time** as you adjust filters

### Workload Views

Save and restore specific configurations as **named workloads**:

**What's Saved in a Workload**:
- Selected subscriptions
- View level (overview, detailed, implementation)
- AI/User layer toggles
- Resource group filters
- Service type filters
- Validation source filters
- Expanded filter categories
- Graph viewport and node positions

**Workflow**:
1. Configure your view (subscriptions, filters, layout)
2. Click **"Save Workload"** in the sidebar
3. Enter a name (e.g., "Production AKS Cluster", "Dev Environment")
4. Later, select the workload from the dropdown to restore the exact view

**Workload Management**:
- **Create**: Save current view state with a name
- **Load**: Restore a saved workload (subscriptions, filters, positions)
- **Update**: Save changes to an existing workload
- **Rename**: Change workload name
- **Delete**: Remove a saved workload

**Dirty State Tracking**:
The UI shows when your current view differs from the saved workload, prompting you to save changes.

**Storage**:
Workloads are stored in `backend/data/workload/workloads.json` and persist across sessions.

## Graph Composition

### Node Filtering

The workload graph displays **monitored resources only** - resources that are actively collected and analyzed:

- **Monitored resources**: Compute (VMs, VMSS), Storage, Databases, Networking (VNets, Load Balancers, App Gateways, etc.)
- **Non-monitored resources**: Subnets, synthetic intermediate resources
- **Automatic bridging**: When non-monitored resources are filtered out, dependency edges are automatically bridged to connect their upstream and downstream nodes, preserving the complete dependency chain

**Example**: If VM → Subnet → NSG dependencies exist, the graph shows VM → NSG (with subnet bridging applied automatically)

### Supported Resource Types

Monitored resource types include:
- **Compute**: Virtual Machines, VM Scale Sets, Kubernetes (AKS)
- **Networking**: VNets, Load Balancers, Application Gateways, Azure Firewall, Public IPs
- **Storage**: Storage Accounts, Disks, NetApp Volumes
- **Database**: SQL Server/Database, SQL Managed Instance, Cosmos DB, MySQL/PostgreSQL
- **Data**: Event Hub, Service Bus, Databricks
- **Web**: App Service, Container Apps, Container Registry
- **Integration**: API Management, Key Vault, Application Insights
- **Recovery**: Recovery Services Vaults

## Multi-Source Dependency Detection

The application uses a **multi-signal approach** to discover Azure resource dependencies with high confidence:

### Detection Methods (13 Signal Types)

The system detects dependencies through multiple independent methods:

| Confidence | Signal Type | Source |
|---|---|---|
| 0.98 | ARM_Declared | Azure Resource Manager properties |
| 0.96 | PrivateEndpoint | Private Endpoint configurations |
| 0.95 | BackendPoolMembership | Load Balancer and Application Gateway backend pools |
| 0.92 | FlowLogObserved | NSG Flow Log analysis |
| 0.90 | ApplicationInsights | App Insights dependency tracking |
| 0.90 | ConnectionString | Config/connection string patterns |
| 0.88 | AppConfig | Key Vault and App Config |
| 0.85 | PrivateDNS | Private DNS zones and records |
| 0.85 | SubnetRouting | AKS subnet routing |
| 0.82 | DNSZoneLink | Azure DNS zone mappings |
| 0.72 | RouteTable | Route table associations |
| 0.70 | VNetCoupling | VNet topology analysis |
| 0.68 | NSGRule | Network Security Group rules |

### Confidence Aggregation

When multiple signals detect the same dependency, confidence scores are combined using Bayesian aggregation:
- Single signal: Base confidence (0.68-0.98)
- Each additional signal: +2% boost
- Multiple signals (3+): Approaches 100% confidence

### User Experience

When viewing a dependency in the UI:
- **Signal Details Panel** shows all detected signals with individual confidence scores
- **Color-coded confidence**: 🟢 Green (≥90%), 🟡 Yellow (70-89%), 🔴 Red (<70%)
- **Aggregated Confidence** combines all signals into a unified score
- **Evidence preservation** maintains full audit trail of detection methods

### LLM Integration

The LLM annotator uses signal confidence to:
- Elevate criticality for high-confidence edges (≥0.9)
- Apply caution for lower-confidence edges (<0.7)
- Justify dependency assessments based on signal evidence

### Extending Dependency Detection

Users can add custom resource reference patterns without writing code:

1. **Edit** `backend/config/reference_definitions.yaml`
2. **Add** new reference patterns following the schema:

```yaml
- source_type: microsoft.network/loadbalancers
  target_type: microsoft.network/publicipaddresses
  relationship: uses_public_ip
  reference_field: frontendIPConfigurations[].properties.publicIPAddress.id
  is_array: true
  confidence: 0.95
  description: Load Balancer uses Public IP
```

3. **Restart** the collector to load new definitions

The system currently includes **20 pre-configured patterns** covering networking, compute, private endpoints, storage, databases, and application gateways. 

## Resiliency Analysis - Workload Discovery & Filtering

The **Resiliency Analysis** module evaluates resources against Azure best practices and presents findings through intelligent filtering:

### Validation Sources

Recommendations are evaluated through multiple **independent validation sources**, each with different confidence levels and detection methods:

| Source | Method | Confidence | Use Case | Example |
|--------|--------|-----------|----------|---------|
| **APRL** | KQL Query | 🟢 Highest | Authoritative Azure best practices | Query confirms multi-region replication configured |
| **Heuristic** | Pattern Matching | 🟡 Medium | Recommendations without KQL queries | Infers encryption from resource properties |
| **LLM** | AI Analysis | 🟡 Medium | Heuristic escalation or complex logic | LLM evaluates application-level retry patterns |
| **ZoneRecommendation** | Zone Analysis | 🟢 High | Availability Zone resilience | Detects single-zone vs multi-zone deployment |

**Hierarchy**: APRL (if query exists) → Heuristic (if no query) → ZoneRecommendation (parallel zone analysis)

### Filtering by Validation Source

Use the **Validation Source filter** in the left sidebar to focus on specific types of recommendations:

**Use Cases**:
- Filter to **APRL only** to see recommendations backed by official Microsoft documentation
- Filter to **Heuristic + LLM** to explore AI-inferred best practices
- Filter to **ZoneRecommendation** to focus on availability zone resilience
- Combine filters to cross-reference recommendations across sources

**How It Works**:
1. Open the sidebar (left side of the workload view)
2. Scroll to **Validation Source** section
3. Check/uncheck sources to include/exclude from scoring
4. Overview stats, scores, and findings update automatically
5. Click "Reset Filters" to restore all sources

**Impact on Scoring**:
When you filter by validation source, the resilience score recalculates using only recommendations from selected sources. For example:
- If you uncheck **Heuristic**, all heuristic-based recommendations are excluded from the score
- Weights are automatically normalized so the remaining recommendations still sum to 100%
- Per-finding contribution percentages update to reflect the filtered set

## Resiliency Correlation & Grouping

The application automatically identifies and visualizes **resilience groups** - collections of resources configured for high availability and fault tolerance:

### Automatic Group Discovery

The system detects 8 types of resilience groups:

| Group Type | Description | Example |
|-----------|-------------|----------|
| **Availability Set** | VMs distributed across fault/update domains | 3 VMs in zones 1,2,3 |
| **VMSS** | Virtual Machine Scale Set instances | Auto-scaling web tier |
| **Load Balancer Backend** | Resources behind a load balancer | 4 VMs in backend pool |
| **Storage Geo-Redundancy** | Geo-replicated storage accounts | GRS/GZRS storage |
| **SQL Failover Group** | Database high availability pairs | Primary + secondary DB |
| **Cosmos DB Replication** | Multi-region Cosmos accounts | Global distribution |
| **Custom Groups** | Tag-based logical groupings | User-defined collections |
| **Cross-Zone Groups** | Resources spanning multiple AZs | Multi-zone deployments |

### Visual Integration

- Groups automatically appear as **graph groups** in the workload visualization
- Resources are visually clustered by their resilience configuration
- Group metadata shows member count, zones, and resilience status
- No manual configuration required

### Context-Aware Recommendations

Resiliency evaluations consider group membership:
- Single-zone VM in an Availability Set: ✅ "Multi-zone resilience achieved through Availability Set"
- Standalone single-zone VM: ⚠️ "Migrate to multi-zone deployment"

## Scoring & Resiliency Calculation

The application uses a **hierarchical, weighted scoring model** to evaluate workload resilience against Azure best practices:

### Resiliency Score Overview

The resilience score represents the overall health of a workload on a scale of **0.0 to 1.0** (0-100%), calculated by aggregating evaluation results from the Azure Proactive Resiliency Library (APRL) against each resource.

### Three-Factor Scoring Formula

Each individual recommendation is weighted by three independent factors:

$$\text{Resiliency Item Weight} = \text{Resource Weight} \times \text{Category Weight} \times \text{Impact Weight}$$

**Score** is calculated as the ratio of weighted passed resiliency item to total weighted resiliency item:

$$\text{Cumulative Resiliency Score} = \frac{\sum_{\text{passed}} \text{Resiliency Item Weight}}{\sum_{\text{all}} \text{Resiliency Item Weight}}$$

### Scoring Factors

#### 1. Element Weight (Criticality)
- **Range**: 0.0 to 1.0+ (derived from LLM criticality scoring)
- **Purpose**: Emphasizes recommendations for critical resources
- **Default**: 1.0 for all resources
- **Customization**: Set via LLM annotations or manual node overrides

#### 2. Category Weight
Weight distribution across 6 resilience dimensions (configured in `app_config.yaml`):

| Category | Default Weight | Purpose |
|---|---|---|
| High Availability | 0.30 | Redundancy, failover, and uptime |
| Disaster Recovery | 0.20 | Backup and recovery procedures |
| Scalability | 0.20 | Auto-scaling and capacity planning |
| Monitoring & Alerting | 0.15 | Observability and incident response |
| Security | 0.10 | Access control and encryption |
| Other Best Practices | 0.05 | General recommendations |

**Note**: Weights sum to 1.0 and are auto-normalized if configured otherwise.

#### 3. Impact Weight
Severity level of each individual recommendation (configured in `app_config.yaml`):

| Impact | Default Weight | Meaning |
|---|---|---|
| High | 0.6 | Critical for production readiness |
| Medium | 0.3 | Important for operational stability |
| Low | 0.1 | Minor improvements and optimization |

### Workload-Level Score

The overall resilience score aggregates resource-level scores using element weights:

$$\text{Workload Score} = \frac{\sum_{\text{passed}} \text{Resiliency Item Weight}}{\sum_{\text{all}} \text{Resiliency Item Weight}}$$

Where each resiliency item weight incorporates its resource's criticality (element weight).

### Category Breakdown

The score is also decomposed by resilience category for targeted improvement:

$$\text{Category Score} = \frac{\sum_{\text{passed, category}} \text{Weighted Resiliency Items}}{\sum_{\text{all, category}} \text{Weighted Resiliency Items}}$$

### Example Calculation

Consider a workload with 2 resources:

**Resource 1** (Virtual Machine - Criticality: 0.8):
- 1 High-Impact HighAvailability Resiliency Item: PASS
  - Weight: 0.8 × 0.30 × 0.6 = 0.144 ✓ (passed)
- 1 Medium-Impact Security Resiliency Item: FAIL
  - Weight: 0.8 × 0.10 × 0.3 = 0.024 ✗ (failed)

**Resource 2** (Database - Criticality: 1.0):
- 1 High-Impact DisasterRecovery Resiliency Item: PASS
  - Weight: 1.0 × 0.20 × 0.6 = 0.120 ✓ (passed)
- 1 Low-Impact Monitoring Resiliency Item: PASS
  - Weight: 1.0 × 0.15 × 0.1 = 0.015 ✓ (passed)

**Workload Score Calculation**:
- Total Passed Weight: 0.144 + 0.120 + 0.015 = 0.279
- Total Weight: 0.144 + 0.024 + 0.120 + 0.015 = 0.303
- **Workload Score**: 0.279 / 0.303 = **0.92 (92%)**

### Score Interpretation

| Score Range | Status | Interpretation |
|---|---|---|
| 0.90 - 1.00 | 🟢 Excellent | Strong resilience posture |
| 0.75 - 0.89 | 🟡 Good | Generally healthy, address medium/high items |
| 0.50 - 0.74 | 🟠 Fair | Needs attention, prioritize high-impact items |
| < 0.50 | 🔴 Poor | Significant resilience gaps |

### Customizing Scoring Weights

Weights are configurable in `backend/config/app_config.yaml`:

```yaml
resilience:
  category_weights:
    "HighAvailability": 0.30
    "DisasterRecovery": 0.20
    "Scalability": 0.20
    "MonitoringAndAlerting": 0.15
    "Security": 0.10
    "OtherBestPractices": 0.05

  impact_weights:
    "High": 0.6
    "Medium": 0.3
    "Low": 0.1
```

After modifying:
1. Restart the API server: `uvicorn app.main:app --reload`
2. Re-run resilience evaluations: `python -m app.resilience.run --subscription-id <id>`
3. Scores will recalculate automatically in the frontend


## Development Workflow

### Full Development Flow

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

3. **Terminal 3 - Data Collection/Analysis** (as needed):
   ```bash
   cd backend
   source .venv/bin/activate
   # Collect resources
   python -m app.collector.run --subscription-id <your-subscription-id>
   # Run resilience evaluations
   python -m app.resilience.run --subscription-id <your-subscription-id>
   # Run LLM annotations (optional)
   python -m app.llm.run --subscription-id <your-subscription-id>
   ```

### Refresh Data

To update the graph with new Azure resources:

1. Re-run the collector: `python -m app.collector.run --subscription-id <id>`
2. Re-run LLM annotations (optional): `python -m app.llm.run --subscription-id <id>`
3. Click "Reload" in the frontend UI or refresh the browser

## Configuration Reference

### Backend Configuration (app_config.yaml)

Set these in `backend/config/app_config.yaml`.

| Section/Key | Required | Default | Description |
|-------------|----------|---------|-------------|
| `llm.enabled` | No | `false` | Toggle LLM end-to-end (compute and serve annotations) |
| `llm.batch_threshold` | No | `50` | Node count threshold for batching annotations |
| `llm.max_nodes_per_batch` | No | `30` | Max nodes per batch when batching |
| `llm.model` / `LLM_MODEL` | Yes (if LLM enabled) | - | Model/deployment name used by the agent |
| `llm.max_attempts` / `LLM_MAX_ATTEMPTS` | No | `2` | Maximum retry attempts |
| `llm.max_tokens` / `LLM_MAX_TOKENS` | No | `6000` | Maximum tokens for LLM response |
| `llm.timeout_seconds` / `LLM_TIMEOUT_SECONDS` | No | `60` | Request timeout in seconds |
| `ai_agent.foundry_project_endpoint` / `AI_FOUNDRY_PROJECT_ENDPOINT` | Yes (if LLM enabled) | - | Foundry project endpoint for agent threads/runs/messages |
| `ai_agent.reasoning_model` / `AI_FOUNDRY_REASONING_MODEL` | No | `gpt-5.4-mini` | Reasoning model used by Foundry agent execution |
| `ai_agent.embedding_model` / `AI_FOUNDRY_EMBEDDING_MODEL` | No | `text-embedding-3-small` | Embedding model used by ingestion/search tooling |
| `ai_agent.chat_agent_reference` / `AI_FOUNDRY_CHAT_AGENT_REFERENCE` | Yes (if chat enabled) | - | Agent reference/id for chat flow |
| `ai_agent.resilience_agent_reference` / `AI_FOUNDRY_RESILIENCE_AGENT_REFERENCE` | Yes (if LLM enabled) | - | Agent reference/id for resilience flow |
| `ai_agent.annotations_agent_reference` / `AI_FOUNDRY_ANNOTATIONS_AGENT_REFERENCE` | Yes (if LLM enabled) | - | Agent reference/id for annotations flow |
| `ai_agent.run_timeout_seconds` | No | `120` | Max wait time for agent runs |
| `ai_agent.poll_interval_seconds` | No | `1.5` | Poll interval while waiting for run completion |
| `data.dir` | No | `./data` | Base directory for collected artifacts |
| `data.monitored_resource_types_path` | No | `./config/monitored_resource_types.yaml` | Allowlist used to tag HA/DR-monitored resource types (collection keeps all resources) |

### Frontend Configuration

The frontend proxies API requests to the backend at `http://127.0.0.1:8000` (configured in `vite.config.ts`).

## Zonal Resiliency Analysis

The application includes a dedicated **Zonal Resiliency** tab that analyzes Azure Availability Zone configuration across all resources:

### Features

- **Compliance Score**: Visual percentage of resources meeting 3-AZ best practices
- **Deployment Pattern Breakdown**: Resources categorized by zone configuration:
  - 🟢 **Zone Redundant**: Platform-managed cross-zone replication
  - 🔵 **Multi-Zone**: Deployed across 2-3 availability zones
  - 🟡 **Single Zone**: Pinned to one zone (risk of zone failure)
  - ⚪ **Unknown**: Configuration unclear or not detected
  - ⚫ **Not Applicable**: Resource type doesn't support zones
- **Regional Analysis**: Zone-enabled vs non-zone regions
- **Interactive Resource Table**: 
  - Sortable by name, pattern, compliance
  - Filterable by deployment pattern
  - Shows assigned zones and 3-AZ status
- **Smart Recommendations**: Actionable guidance based on current deployment

### Access

1. Run resilience evaluation: `python -m app.resilience.run --subscription-id <id>`
2. Start the frontend application
3. Select your subscription
4. Click the **"Zonal Resiliency"** tab (🌍 icon)

## Terraform Configuration Analysis

Analyze infrastructure **before deployment** by importing Terraform configurations:

### Use Cases

- **Pre-deployment validation**: Identify resilience issues before provisioning
- **Infrastructure review**: Audit existing Terraform code
- **Offline analysis**: No Azure subscription or permissions required

### Quick Start

```bash
# Import from directory
python -m app.terraform.run --terraform-dir ./my-terraform

# Or single file
python -m app.terraform.run --terraform-file ./main.tf

# Then analyze
python -m app.resilience.run --subscription-id <generated-id>
python -m app.llm.run --subscription-id <generated-id>  # Optional
```

### Supported Resources

30+ Azure resource types including:
- Compute: VMs, VMSS, AKS, Container Instances, App Services
- Storage: Storage Accounts, Managed Disks, File Shares
- Databases: SQL, PostgreSQL, MySQL, Cosmos DB, Redis
- Networking: VNets, Subnets, NSGs, Load Balancers, Application Gateways
- Platform: Key Vault, App Configuration, Service Bus, Event Hubs

### Features

- **HCL and JSON support**: Parses both `.tf` and Terraform JSON state files
- **SKU extraction**: Captures tier, size, and zone configuration
- **Relationship detection**: Identifies dependencies between resources
- **Full pipeline compatibility**: Works with all resilience and LLM modules

## AI-Powered Chat Assistant

The application includes an **intelligent chat assistant** that helps analyze infrastructure, answer questions, and provide remediation guidance through Azure AI Foundry Agents.

### Features

- **Natural Language Queries**: Ask questions about your infrastructure in plain English
- **Contextual Understanding**: Scope classification and strict ID validation keep responses workload-focused
- **Query-Type-Specific Responses**: Different response formats for different types of questions:
  - **Findings**: Explains failures and impact
  - **Remediation**: Provides step-by-step fix instructions
  - **Terraform**: Generates infrastructure-as-code
  - **Connections**: Suggests logical dependencies between resources
  - **General**: Answers infrastructure questions, including cost and performance impact
- **Smart Recommendations**: Structured, actionable recommendations with priority, effort, and impact assessment
- **Resource Highlighting**: Automatically highlights relevant resources in the graph
- **Edit & Retry**: Edit and resend messages, retry failed queries
- **Baseline Summaries**: Contextual summaries from LLM annotations

### Query Examples

The chat assistant can answer questions like:

- "Why is this resource failing resilience checks?"
- "How do I fix this VM for high availability?"
- "Generate Terraform code for zone redundancy"
- "What are the top 3 changes I need to make to improve resilience?"
- "What would be the cost difference for ZRS disk? Will this change impact performance?"
- "Show me the dependencies for this VM"
- "Which resources need updating for compliance?"

### Configuration

Chat requires Foundry project configuration in `backend/.env`:

For deployed VM runtime, this endpoint is injected automatically into `/etc/azure-resilience-iq.env` by the deployment script.

```env
AI_FOUNDRY_PROJECT_ENDPOINT=https://<your-foundry-resource>.services.ai.azure.com/api/projects/<project-name>
```

If you do not have this endpoint, contact the repository maintainer.

### UI Integration

The chat panel appears as a floating badge on both the **Graph** and **Overview** tabs:

1. Click the chat copilot icon to open
2. Type your question in natural language
3. View responses with:
   - Structured recommendations with priority and effort
   - Resource chips you can click to highlight in graph
   - Code blocks for Terraform generation
   - Actionable edge suggestions
4. Edit and resend messages using the edit button
5. Retry failed queries with the retry button

The chat is context-aware:
- Knows which tab you're viewing (graph vs overview)
- Understands selected resources
- Provides relevant recommendations based on findings

### Response Validation

All LLM outputs are validated before being returned:

- **Edge suggestions**: Filtered to prevent illogical connections (e.g., disks from different VMs)
- **Resource references**: Verified against the graph
- **Terraform code**: Self-validated using built-in validation prompts
- **Query-type filtering**: Edges and terraform only generated when explicitly requested

## Troubleshooting

### Collector Issues

**Problem**: `az login` required
- **Solution**: Run `az login` and `az account set --subscription <id>`

**Problem**: No resources found
- **Solution**: Verify subscription ID and ensure you have read permissions

### LLM Annotation Issues

**Problem**: "LLM gateway unavailable" or agent calls fail
- **Solution**: Ensure `llm.enabled: true`, `AI_FOUNDRY_PROJECT_ENDPOINT`, and all flow-specific agent references (`chat/resilience/annotations`) are correctly configured.

**Problem**: No annotations generated
- **Solution**: Confirm `llm.enabled` is `true`, the Foundry project endpoint is valid, and the configured annotations agent exists.

### Chat Issues

**Problem**: Chat feature not visible in UI
- **Solution**: Check `llm.enabled`, `AI_FOUNDRY_PROJECT_ENDPOINT`, and flow-specific agent references (`chat/resilience/annotations`).
- **Verification**: Check `/api/chat/availability` endpoint - it should return `{"available": true}`

**Problem**: Queries rejected as out-of-scope
- **Solution**: Query scope classifier rejected the request; rephrase toward workload resources, dependencies, findings, or remediation.

**Problem**: LLM suggesting invalid edges
- **Solution**: This should not occur - edge suggestions are filtered by query type and logical validation. If you see invalid edges, report as a bug.

**Problem**: Chat returns "LLM service is not available"
- **Solution**: Verify `AI_FOUNDRY_PROJECT_ENDPOINT`, agent references, and managed identity/RBAC access.

**Problem**: Slow chat responses
- **Solution**: Foundry runs are asynchronous. Reduce prompt size, tune polling/timeouts, and check Foundry/backend latency.

**Problem**: `conversation_not_found` errors in backend logs
- **Solution**: Backend now auto-recovers by creating a new conversation, replaying context, and retrying once. If errors persist, run app-only deploy to ensure latest backend code is active.

### Deployment/Provisioning Issues

**Problem**: Foundry portal "Data + indexes" appears empty
- **Cause**: This view may not show external Azure AI Search indexes attached via project connections/tools.
- **Verification**: Rely on provisioning logs and VM-side diagnostics (`--agents-migrate` run output and agent/tool markers), not only portal index tab visibility.

**Problem**: Agent tools seem stale after changes
- **Solution**: Re-run with migration mode:
  - `bash backend/deploy/scripts/deploy_vm_stack.sh backend/deploy/vm-terraform/terraform.tfvars --agents-migrate`

**Problem**: App-only deployment rsync permission errors
- **Solution**: Use latest `deploy_app_only.sh` (includes ownership + sudo-rsync handling). Re-run the same command.

### Backend Issues

**Problem**: Port 8000 already in use
- **Solution**: Change port with `--port 8001` or kill the existing process

**Problem**: Module not found
- **Solution**: Ensure virtual environment is activated and dependencies installed

### Frontend Issues

**Problem**: API calls fail
- **Solution**: Verify backend is running on port 8000

**Problem**: Icons not displaying
- **Solution**: Check that `frontend/public/Icons/` directory contains Azure icon files

## License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

## Contributing

We welcome contributions to this project! Here are some ways you can help:

1. **Reporting Issues**: If you encounter any bugs or have suggestions for improvements, please open an issue in the GitHub repository.
2. **Feature Requests**: If you have an idea for a new feature, feel free to submit a feature request.
3. **Submitting Pull Requests**: If you want to contribute code, please fork the repository, make your changes, and submit a pull request. Ensure your code adheres to the project's coding standards and includes appropriate tests.
4. **Documentation**: Help improve the documentation by suggesting edits or adding new content.

### Guidelines
- Please ensure your contributions are aligned with the project's goals.
- Follow the coding style used in the project.
- Write clear commit messages that explain your changes.

Thank you for your interest in contributing!
