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

## Workflow: Collector → Annotator → API

### Step 1: Run the Azure Resource Graph collector
```bash
cd backend
python -m app.collector.run --subscription-id <your-subscription-id>
```

This generates `backend/data/collector/resources.json`.

### Step 2: Run the LLM annotator
```bash
cd backend
python -m app.llm.run --workload-id demo
```

This processes the collected resources with the LLM and saves annotations to `backend/data/llm_annotations/`.
(Takes 10–15 seconds depending on graph size.)

### Step 3: Start the API server
```bash
cd backend
uvicorn app.main:app --reload
```

### Step 4: Request annotations via API
All annotations are now pre-computed and served instantly:

```bash
curl "http://localhost:8000/api/workloads/demo/graph?include_llm=true"
```

Or toggle **"Show AI-assisted annotations"** in the frontend.

## Notes

- **`.env` is gitignored** – never commit credentials.
- **`.env.example` is tracked** – use it as a template for setting up new environments.
- **DefaultAzureCredential** avoids hardcoding API keys; prefer it over static keys.
- **Annotator is optional** – skip step 2 if you want to test without LLM suggestions.
- **API doesn't compute annotations** – all results are pre-computed for instant response times.
