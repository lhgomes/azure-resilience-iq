# Environment Setup

## Local Development with Azure OpenAI

To enable LLM-based architecture annotation in your local environment:

### 1. Copy the example file
```bash
cd backend
cp .env.example .env
```

### 2. Fill in your Azure OpenAI credentials
Edit `backend/.env` and set:

```env
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=<your-deployment-name>
USE_REAL_LLM=true
```

Get these values from your Azure OpenAI resource in the Azure Portal.

### 3. Ensure DefaultAzureCredential is configured
The backend uses **DefaultAzureCredential**, which checks (in order):
1. Environment variables (`AZURE_*`)
2. Managed Identity (if running in Azure)
3. Azure CLI credentials (`az login`)
4. Visual Studio credentials
5. IntelliJ credentials

For local development, run:
```bash
az login
```

## Workflow: Collector → Resilience Evaluation → LLM Annotation → API

### Step 1: Run the Azure Resource Graph collector
```bash
cd backend
python -m app.collector.run --subscription-id <your-subscription-id>
```

This generates `backend/data/{subscription-id}/resources.json` and `backend/data/{subscription-id}/edges.json`.

If you want to store artifacts somewhere else, set `AZURE_WORKLOAD_GRAPH_DATA_DIR` (default: `data`).

### Step 2: Run resilience evaluations
```bash
cd backend
python -m app.resilience.run --subscription-id <your-subscription-id>
```

This evaluates resources against Azure Proactive Resiliency Library (APRL) and saves results to `backend/data/{subscription-id}/resilience_evaluations.json`.
(Takes 5–10 seconds depending on resource count.)

### Step 3: Run the LLM annotator (optional)
```bash
cd backend
python -m app.llm.run --subscription-id <your-subscription-id>
```

This processes the collected resources with the LLM and saves annotations to `backend/data/{subscription-id}/llm_annotations.json`.
(Takes 10–15 seconds depending on graph size.)
Requires `USE_REAL_LLM=true` and Azure OpenAI configuration.

### Step 4: Start the API server
```bash
cd backend
uvicorn app.main:app --reload
```

### Step 5: Access the API
All data is now pre-computed and served instantly:

```bash
# Get workload graph
curl "http://localhost:8000/api/subscriptions/<your-subscription-id>/graph"

# Get resilience evaluations
curl "http://localhost:8000/api/resilience/evaluate/<your-subscription-id>"

# Get unified recommendations
curl "http://localhost:8000/api/<your-subscription-id>/recommendations"
```

Or start the frontend and toggle visibility options in the UI.

## Application Configuration (app_config.yaml)

The `backend/config/app_config.yaml` file controls application behavior and resilience analysis settings.

### Logging Configuration

```yaml
logging:
  level: "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL
```

- **level**: Controls logging verbosity. Can be overridden by `LOG_LEVEL` environment variable.

### LLM Configuration

```yaml
llm:
  use_real_llm: true           # Enable/disable LLM features
  annotation_enabled: true     # Include LLM annotations in API responses
```

- **use_real_llm**: Enable/disable the LLM annotation engine. Set to `false` to skip LLM processing entirely (useful for testing without Azure OpenAI).
- **annotation_enabled**: Control whether LLM annotations appear in API responses even when pre-computed.

### Resilience Analysis Configuration

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

  aprl_root: "aprl"
  rules_dir: "./config/resiliency_rules"
```

**Category Weights** (must sum to 1.0):
- **HighAvailability** (0.30): Redundancy, failover, and availability patterns
- **DisasterRecovery** (0.20): Backup, restoration, and recovery procedures
- **Scalability** (0.20): Auto-scaling, performance, and capacity planning
- **MonitoringAndAlerting** (0.15): Observability, logging, and alerting
- **Security** (0.10): Access control, encryption, and compliance
- **OtherBestPractices** (0.05): General best practices and recommendations

**Impact Weights** (must sum to 1.0):
- **High** (0.6): Critical recommendations that significantly affect resilience
- **Medium** (0.3): Important recommendations with moderate impact
- **Low** (0.1): Minor recommendations and optimizations

**Paths**:
- **aprl_root**: Location of the Azure Proactive Resiliency Library v2 (relative to backend directory or absolute path)
- **rules_dir**: Directory containing custom resiliency rule definitions

### Customizing Configuration

To modify settings:

1. Edit `backend/config/app_config.yaml`
2. Restart the API server (`uvicorn app.main:app --reload`)

Example: To increase weight for Security and reduce Others:
```yaml
resilience:
  category_weights:
    "HighAvailability": 0.25
    "DisasterRecovery": 0.20
    "Scalability": 0.20
    "MonitoringAndAlerting": 0.15
    "Security": 0.15        # Increased from 0.10
    "OtherBestPractices": 0.05
```

The weights will be auto-normalized if they don't sum to exactly 1.0.

## Notes

- **`.env` is gitignored** – never commit credentials.
- **`.env.example` is tracked** – use it as a template for setting up new environments.
- **DefaultAzureCredential** avoids hardcoding API keys; prefer it over static keys.
- **Resilience evaluation is recommended** – provides APRL-based recommendations before optional LLM processing.
- **LLM Annotator is optional** – skip step 3 if you want to test without LLM suggestions.
- **API doesn't compute evaluations** – all results are pre-computed for instant response times.
