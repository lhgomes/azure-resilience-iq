# Azure Workload Graph

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
cd azure-workload-graph
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

#### Configure Environment Variables

Create a `.env` file in the `backend/` directory with the following content:

```bash
# Azure OpenAI Configuration (required for LLM annotations)
# Set USE_REAL_LLM=true to enable LLM-powered annotations
USE_REAL_LLM=true

# Azure OpenAI Endpoint and Deployment
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=your-deployment-name

# Optional: API Version (default: 2024-05-01-preview)
AZURE_OPENAI_API_VERSION=2024-05-01-preview

# Optional: Timeout and retry settings
AZURE_OPENAI_TIMEOUT_SECONDS=60
AZURE_OPENAI_MAX_ATTEMPTS=2
AZURE_OPENAI_MAX_TOKENS=6000
```

**Note**: If you don't have Azure OpenAI or want to skip LLM annotations, set `USE_REAL_LLM=false` or omit it entirely.

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

This creates:
- `data/{subscription-id}/resources.json` - Collected Azure resources with subscription metadata
- `data/{subscription-id}/edges.json` - Multi-source dependency edges with signal details

Data is organized by subscription ID. To use a different base directory, set `AZURE_WORKLOAD_GRAPH_DATA_DIR`.

### Step 2: Run LLM Annotations (Optional)

If you configured Azure OpenAI and set `USE_REAL_LLM=true`, run the LLM annotator:

```bash
python -m app.llm.run --subscription-id <your-subscription-id>
```

This analyzes the collected resources and generates:
- Display name suggestions
- Layer classifications (L1: core workload, L2: network/platform, L3: implementation details)
- Criticality scores (1-10) and criticality weights (% distribution)
- Architecture improvement suggestions

Results are saved to `data/{subscription-id}/llm_annotations.json`.

### Step 3: Start the Backend Server

Run the FastAPI backend:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The backend API will be available at `http://localhost:8000`.

**API Health Check**:
```bash
curl http://localhost:8000/health
```

### Step 4: Start the Frontend

In a new terminal:

```bash
cd frontend
npm run dev
```

The frontend will be available at `http://localhost:5173`.

**First Time**: When you first open the app, you'll see a subscription picker. Select the subscription you collected data for, and the graph will load automatically.

## Project Structure

```
azure-workload-graph/
├── backend/
│   ├── app/
│   │   ├── collector/        # Azure Resource Graph collector
│   │   ├── graph/            # Graph building and modeling
│   │   ├── llm/              # LLM annotation engine
│   │   ├── relationships/    # Resource relationship extraction
│   │   ├── services/         # Business logic (workloads, subscriptions)
│   │   ├── storage/          # Data persistence layer
│   │   ├── intent/           # User overrides and manual edges
│   │   └── main.py           # FastAPI application
│   ├── data/
│   │   └── {subscription-id}/
│   │       ├── resources.json        # Collected Azure resources
│   │       ├── edges.json            # Multi-source dependency edges
│   │       ├── llm_annotations.json  # LLM-generated annotations
│   │       ├── node_overrides.json   # User node customizations
│   │       ├── edge_overrides.json   # Edge accept/reject decisions
│   │       ├── manual_edges.json     # User-created edges
│   │       └── groups.json           # Node groupings
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

## API Endpoints

The backend provides the following main endpoints:

### Core Endpoints
- `GET /health` - Health check
- `GET /api/subscriptions` - List available subscriptions
- `GET /api/subscriptions/{subscription_id}/graph` - Get workload graph

### Node Management
- `PATCH /api/subscriptions/{subscription_id}/nodes/{node_id}` - Update node properties (name, color, icon, layer, criticality_score)
- `DELETE /api/subscriptions/{subscription_id}/nodes/{node_id}` - Remove node override

### Edge Management
- `POST /api/subscriptions/{subscription_id}/edges` - Create manual edge
- `POST /api/subscriptions/{subscription_id}/edges/{edge_id}/accept` - Accept edge
- `POST /api/subscriptions/{subscription_id}/edges/{edge_id}/reject` - Reject edge
- `POST /api/subscriptions/{subscription_id}/edges/{edge_id}/reverse` - Reverse edge direction
- `DELETE /api/subscriptions/{subscription_id}/edges/{edge_id}` - Delete edge

### Group Management
- `POST /api/subscriptions/{subscription_id}/groups` - Create a new group
- `PATCH /api/subscriptions/{subscription_id}/groups/{group_id}` - Update group name
- `DELETE /api/subscriptions/{subscription_id}/groups/{group_id}` - Delete a group
- `POST /api/subscriptions/{subscription_id}/groups/{group_id}/nodes` - Add node to group
- `DELETE /api/subscriptions/{subscription_id}/groups/{group_id}/nodes/{node_id}` - Remove node from group

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

3. **Terminal 3 - Data Collection/LLM** (as needed):
   ```bash
   cd backend
   source .venv/bin/activate
   # Collect resources
   python -m app.collector.run --subscription-id <your-subscription-id>
   # Run LLM annotations
   python -m app.llm.run --subscription-id <your-subscription-id>
   ```

### Refresh Data

To update the graph with new Azure resources:

1. Re-run the collector: `python -m app.collector.run --subscription-id <id>`
2. Re-run LLM annotations (optional): `python -m app.llm.run --subscription-id <id>`
3. Click "Reload" in the frontend UI or refresh the browser

## Configuration Reference

### Backend Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `USE_REAL_LLM` | No | `false` | Enable/disable LLM annotations |
| `AZURE_OPENAI_ENDPOINT` | Yes (if LLM enabled) | - | Azure OpenAI service endpoint |
| `AZURE_OPENAI_DEPLOYMENT` | Yes (if LLM enabled) | - | Azure OpenAI deployment name |
| `AZURE_OPENAI_API_VERSION` | No | `2024-05-01-preview` | Azure OpenAI API version |
| `AZURE_OPENAI_TIMEOUT_SECONDS` | No | `60` | Request timeout in seconds |
| `AZURE_OPENAI_MAX_ATTEMPTS` | No | `2` | Maximum retry attempts |
| `AZURE_OPENAI_MAX_TOKENS` | No | `6000` | Maximum tokens for LLM response |

### Frontend Configuration

The frontend proxies API requests to the backend at `http://127.0.0.1:8000` (configured in `vite.config.ts`).

## Troubleshooting

### Collector Issues

**Problem**: `az login` required
- **Solution**: Run `az login` and `az account set --subscription <id>`

**Problem**: No resources found
- **Solution**: Verify subscription ID and ensure you have read permissions

### LLM Annotation Issues

**Problem**: "AZURE_OPENAI_ENDPOINT not set"
- **Solution**: Configure `.env` file with Azure OpenAI credentials

**Problem**: No annotations generated
- **Solution**: Ensure `USE_REAL_LLM=true` in `.env` and Azure OpenAI is properly configured

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
