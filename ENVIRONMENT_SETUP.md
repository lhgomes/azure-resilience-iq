# Environment Setup

## Local Development with APIM + Azure AI Foundry Agent

The backend LLM runtime is APIM + Azure AI Foundry only.
Direct Azure OpenAI SDK configuration is no longer used by runtime modules.

### 1. Configure `backend/config/app_config.yaml`

```yaml
llm:
  enabled: true

ai_agent:
  gateway_base_url: "https://<apim-host>/<agent-api-base>"
  agent_id: "asst_<foundry-agent-id>"
  api_version: "2025-05-01"
  subscription_header_name: "api-key"
  memory_scope: "subscription_or_workload"

  # Optional: APIM embeddings endpoint used by ingestion/RAG tooling
  embedding_base_url: "https://<apim-host>/<openai-api-base>"
  embedding_api_version: "2024-10-21"
  embedding_subscription_header_name: "api-key"
```

### 2. Configure `backend/.env`

```env
AZURE_SEARCH_ENDPOINT=<your-search-endpoint>
AZURE_SEARCH_ADMIN_KEY=<your-search-admin-key>
AZURE_SEARCH_INDEX_NAME=<your-index-name>

AI_GATEWAY_SUBSCRIPTION_KEY=<required-apim-subscription-key>
```

`AI_GATEWAY_SUBSCRIPTION_KEY` is required to call APIM routes.
If you do not have one, contact the repository maintainer.

## Workflow: Collector → Resilience Evaluation → LLM Annotation → API

### Step 1: Run the Azure Resource Graph collector
```bash
cd backend
python -m app.collector.run --subscription-id <your-subscription-id>
```

This generates `backend/data/{subscription-id}/resources.json` and `backend/data/{subscription-id}/edges.json`.

### Step 2: Run resilience evaluations
```bash
cd backend
python -m app.resilience.run --subscription-id <your-subscription-id>
```

This evaluates resources against APRL and saves results to `backend/data/{subscription-id}/resilience_evaluations.json`.

### Step 3: Run the LLM annotator (optional)
```bash
cd backend
python -m app.llm.run --subscription-id <your-subscription-id>
```

This generates annotations in `backend/data/{subscription-id}/llm_annotations.json`.
Requires `llm.enabled: true` and valid APIM + Foundry agent configuration.

### Step 4: Start the API server
```bash
cd backend
uvicorn app.main:app --reload
```

### Step 5: Access the API
```bash
# Get workload graph
curl "http://localhost:8000/api/subscriptions/<your-subscription-id>/graph"

# Get resilience evaluations
curl "http://localhost:8000/api/resilience/evaluate/<your-subscription-id>"

# Get unified recommendations
curl "http://localhost:8000/api/<your-subscription-id>/recommendations"
```

## Application Configuration (`app_config.yaml`)

### Logging
```yaml
logging:
  level: "INFO"
```

### LLM
```yaml
llm:
  enabled: true
```

### APIM + Foundry Agent
```yaml
ai_agent:
  gateway_base_url: "..."
  agent_id: "asst_..."
  api_version: "2025-05-01"
  subscription_header_name: "api-key"
  memory_scope: "subscription_or_workload"
```

### Resilience
```yaml
resilience:
  category_weights: { ... }
  impact_weights: { ... }
  aprl_root: "aprl"
  rules_dir: "./config/resiliency_rules"
```

## Notes

- `.env` is gitignored. Never commit credentials.
- `.env.example` is tracked. Use it as a template.
- APRL deterministic evaluation remains the source of truth.
- Agent memory is used as continuity/context; memory keys are scoped by subscription/workload/module.
