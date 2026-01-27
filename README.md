# Azure Resiliency IQ

A full-stack application for visualizing and analyzing Azure workloads using Azure Resource Graph and LLM-powered annotations.

## Prerequisites

### Required Software

- **Python 3.12+** - Required for the backend
- **Node.js 18+** and **npm** - Required for the frontend
- **Azure CLI** - Required for Azure authentication

### Azure Requirements

- Azure subscription with resources to analyze
- Azure OpenAI service (optional, for LLM annotations)
- Appropriate Azure RBAC permissions to query resources

## Installation

### 1. Clone the Repository

```bash
git clone <repository-url>
cd azure-resilience-iq
```

### 2. Backend Setup

#### Install Python Dependencies

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e .
```

Notes:
- `pip install -e .` is supported (the backend packages only the `app/` module).
- `data/` contains runtime artifacts (collector output, overrides, annotations) and is intentionally not packaged.

#### Configure Application Settings

Edit `backend/config/app_config.yaml` to set Azure OpenAI and LLM settings:

```yaml
llm:
  enabled: true
  batch_threshold: 50
  max_nodes_per_batch: 30

azure_openai:
  endpoint: "https://your-resource.openai.azure.com/"
  deployment: "your-deployment-name"
  api_version: "2024-05-01-preview"
  timeout_seconds: 60
  max_attempts: 2
  max_tokens: 6000
  api_key: ""  # optional; leave empty to use DefaultAzureCredential
```

Set `llm.enabled` to `false` to skip LLM calls and omit annotations from responses.

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
3. Click "📦 Import Terraform Configuration"
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

If you configured Azure OpenAI and have `llm.enabled: true`, run the LLM annotator:

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
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The backend API will be available at `http://localhost:8000`.

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

## Project Structure

```
azure-resilience-iq/
├── backend/
│   ├── app/
│   │   ├── collector/        # Azure Resource Graph collector
│   │   ├── graph/            # Graph building and modeling
│   │   ├── llm/              # LLM annotation engine
│   │   ├── resilience/       # Resiliency evaluation and APRL integration
│   │   ├── relationships/    # Resource relationship extraction
│   │   ├── routes/           # API route handlers (resilience, recommendations)
│   │   ├── services/         # Business logic (workloads, subscriptions, recommendations)
│   │   ├── storage/          # Data persistence layer
│   │   ├── intent/           # User overrides and manual edges
│   │   ├── config.py         # Configuration utilities
│   │   ├── settings.py       # Settings and environment configuration
│   │   └── main.py           # FastAPI application
│   ├── data/
│   │   └── {subscription-id}/
│   │       ├── resources.json               # Collected Azure resources
│   │       ├── edges.json                  # Multi-source dependency edges
│   │       ├── llm_annotations.json        # LLM-generated annotations
│   │       ├── resilience_evaluations.json # Resiliency scores and recommendations
│   │       ├── node_overrides.json         # User node customizations
│   │       ├── edge_overrides.json         # Edge accept/reject decisions
│   │       ├── manual_edges.json           # User-created edges
│   │       ├── resilience_overrides.json   # Resiliency evaluation overrides
│   │       └── groups.json                 # Node groupings
│   ├── pyproject.toml        # Python dependencies
│   └── .env                  # Environment configuration
├── frontend/
│   ├── src/
│   │   ├── components/       # React components (nodes, edges, graph canvas)
│   │   ├── pages/            # Page components
│   │   ├── domain/           # Business logic (graph view builder)
│   │   ├── api/              # API client functions
│   │   └── utils/            # Utilities and icon resolver
│   ├── public/               # Static assets (Azure icons)
│   ├── package.json          # npm dependencies
│   └── vite.config.ts        # Vite configuration
└── README.md
```

## Multi-Source Dependency Detection

The application uses a **multi-signal approach** to discover Azure resource dependencies with high confidence:

### Detection Methods (12 Signal Types)

The system detects dependencies through multiple independent methods:

| Confidence | Signal Type | Source |
|---|---|---|
| 0.98 | ARM_Declared | Azure Resource Manager properties |
| 0.96 | PrivateEndpoint | Private Endpoint configurations |
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

## API Endpoints

The backend provides the following main endpoints:

### Core Endpoints
- `GET /health` - Health check
- `GET /api/subscriptions` - List available subscriptions
- `GET /api/subscriptions/{subscription_id}/graph` - Get workload graph
- `GET /api/subscriptions/{subscription_id}/reviews` - Get review inbox

### Node Management
- `PATCH /api/subscriptions/{subscription_id}/nodes/{node_id}` - Update node properties (name, color, icon, layer, criticality_score)
- `PATCH /api/subscriptions/{subscription_id}/nodes/{node_id}/criticality` - Update node criticality score
- `DELETE /api/subscriptions/{subscription_id}/nodes/{node_id}` - Remove node override
- `DELETE /api/subscriptions/{subscription_id}/nodes/{node_id}/criticality` - Delete criticality override

### Edge Management
- `POST /api/subscriptions/{subscription_id}/edges` - Create manual edge
- `POST /api/subscriptions/{subscription_id}/edges/{edge_id}/accept` - Accept edge
- `POST /api/subscriptions/{subscription_id}/edges/{edge_id}/reject` - Reject edge
- `POST /api/subscriptions/{subscription_id}/edges/{edge_id}/reverse` - Reverse edge direction
- `DELETE /api/subscriptions/{subscription_id}/edges/{edge_id}` - Delete edge

### Group Management
- `GET /api/subscriptions/{subscription_id}/groups` - List groups
- `POST /api/subscriptions/{subscription_id}/groups` - Create a new group
- `PATCH /api/subscriptions/{subscription_id}/groups/{group_id}` - Update group name
- `DELETE /api/subscriptions/{subscription_id}/groups/{group_id}` - Delete a group
- `POST /api/subscriptions/{subscription_id}/groups/{group_id}/nodes` - Add node to group
- `DELETE /api/subscriptions/{subscription_id}/groups/{group_id}/nodes/{node_id}` - Remove node from group

### Resiliency & Recommendations Endpoints
- `GET /api/resilience/health` - Resiliency module health check
- `GET /api/resilience/rules` - Get all resilience rules (with optional filtering by resource_type and category)
- `GET /api/resilience/evaluate/{subscription_id}` - Get resilience evaluations for subscription
- `GET /api/resilience/evaluate/{subscription_id}/resource/{resource_id}` - Get resilience evaluation for specific resource
- `POST /api/resilience/evaluate/{subscription_id}/refresh` - Refresh resilience evaluations
- `GET /api/resilience/weights` - Get resilience category weights
- `GET /api/resilience/categories` - Get available resilience categories
- `GET /api/resilience/evaluate/{subscription_id}/summary` - Get resilience summary
- `GET /api/resilience/evaluate/{subscription_id}/zonal-resilience` - Get zonal resilience analysis
- `GET /api/resilience/{subscription_id}/overrides` - List resilience evaluation overrides
- `POST /api/resilience/{subscription_id}/overrides` - Create resilience override
- `DELETE /api/resilience/{subscription_id}/overrides` - Delete resilience override
- `GET /api/resilience/{subscription_id}/overrides/check` - Check if resource has overrides
- `GET /api/{subscription_id}/recommendations` - Get unified recommendations (WARA + resilience)
- `GET /api/{subscription_id}/resources/{resource_id}/recommendations` - Get recommendations for specific resource
- `GET /api/{subscription_id}/recommendations/by-category/{category}` - Get recommendations by category
- `GET /api/{subscription_id}/recommendations/summary` - Get recommendations summary

### Workload Management Endpoints
- `GET /api/workloads` - List all saved workload views
- `GET /api/workloads/{workload_id}` - Get a specific workload view
- `POST /api/workloads` - Create a new workload view
  - Body: `{"name": "string", "view_state": {...}}`
- `PATCH /api/workloads/{workload_id}` - Update workload name or view state
  - Body: `{"name": "string" (optional), "view_state": {...} (optional)}`
- `DELETE /api/workloads/{workload_id}` - Delete a workload view

### Subscription Refresh Endpoints
- `POST /api/subscriptions/{subscription_id}/refresh` - Start async LLM annotation refresh
- `GET /api/subscriptions/{subscription_id}/refresh/status` - Check refresh job status

### Terraform Endpoints
- `POST /api/terraform/upload` - Upload and process Terraform files (.tf or .json)
- Form parameters: `files` (multi-file upload), `subscription_id` (optional), `subscription_name` (optional)

## Development Workflow

### Full Development Flow

1. **Terminal 1 - Backend**:
   ```bash
   cd backend
   source .venv/bin/activate
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
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
| `azure_openai.endpoint` | Yes (if LLM enabled) | - | Azure OpenAI service endpoint |
| `azure_openai.deployment` | Yes (if LLM enabled) | - | Azure OpenAI deployment name |
| `azure_openai.api_version` | No | `2024-05-01-preview` | Azure OpenAI API version |
| `azure_openai.timeout_seconds` | No | `60` | Request timeout in seconds |
| `azure_openai.max_attempts` | No | `2` | Maximum retry attempts |
| `azure_openai.max_tokens` | No | `6000` | Maximum tokens for LLM response |
| `azure_openai.api_key` | No | empty | API key; if empty, uses DefaultAzureCredential |
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

## Troubleshooting

### Collector Issues

**Problem**: `az login` required
- **Solution**: Run `az login` and `az account set --subscription <id>`

**Problem**: No resources found
- **Solution**: Verify subscription ID and ensure you have read permissions

### LLM Annotation Issues

**Problem**: "Azure OpenAI endpoint not set"
- **Solution**: Set `azure_openai.endpoint` and `azure_openai.deployment` in `backend/config/app_config.yaml`, and ensure `llm.enabled` is `true`.

**Problem**: No annotations generated
- **Solution**: Confirm `llm.enabled` is `true`, Azure OpenAI settings are populated, and (if no `api_key`) you are logged in with `az login`.

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

This project is licensed under the Creative Commons Attribution 4.0 International License (CC BY 4.0). You are free to:

- Share — copy and redistribute the material in any medium or format
- Adapt — remix, transform, and build upon the material

As long as you follow the license terms:

- You must give appropriate credit, provide a link to the license, and indicate if changes were made. You may do so in any reasonable manner, but not in any way that suggests the licensor endorses you or your use.
- If you remix, transform, or build upon the material, you must distribute your contributions under the same license as the original.

For more details, visit [Creative Commons](https://creativecommons.org/licenses/by/4.0/).

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
