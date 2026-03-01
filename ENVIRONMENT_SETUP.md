# Environment Setup

## Deployment Modes

This project supports two execution modes:

1. **Automated Azure VM deployment (recommended)**
2. **Local development**

Use the automated VM path for shared/test/prod-like environments.

---

## Automated Azure VM Deployment (Recommended)

### Terraform approach (`backend/deploy/vm-terraform`)

Terraform provisions and manages:

- Linux VM + network (VNet/subnets/NSG/public IP)
- Private endpoints + DNS for Foundry/Search
- Azure AI Foundry account/project (`AIServices`)
- Model deployments:
  - reasoning: `gpt-4.1`
  - embeddings: `text-embedding-3-small`
- Azure AI Search
- Required RBAC for VM managed identity (Foundry/OpenAI/Search)

### Required tfvars

Configure `backend/deploy/vm-terraform/terraform.tfvars`:

- `location`
- `resource_group_name`
- `embedding_model_capacity` (current validated env max: `350`)

### Full deployment command

```bash
cd backend/deploy/scripts
bash deploy_vm_stack.sh ../vm-terraform/terraform.tfvars
```

Force agent recreation/tool reattachment when needed:

```bash
bash deploy_vm_stack.sh ../vm-terraform/terraform.tfvars --agents-migrate
```

### Provisioning flow (automated)

1. Terraform apply (infra + Foundry + model deployments)
2. Foundry project connection creation/validation (`azure-ai-search-default`)
3. Search index ensure:
   - `learn-aprl-index`
   - `learn-terraform-index`
4. Agent ensure + tool attachments:
   - `chat-agent` → APRL index + MCP Learn
   - `resilience-agent` → APRL index + MCP Learn
   - `terraform-compiler-agent` → Terraform index
   - `annotations-agent` → no tools
5. RAG refresh into `backend/agent/rag` (staged swap only on successful refresh)
6. Index hydration (embeddings + upload) using only `backend/agent/rag` for Terraform corpus
7. Frontend build + backend/nginx restart

---

## Incremental Update Options (Existing VM)

### Option A: Full stack refresh

Use when Terraform/Foundry/model/agent/index provisioning changed:

```bash
cd backend/deploy/scripts
bash deploy_vm_stack.sh ../vm-terraform/terraform.tfvars
```

### Option B: App-only incremental deploy

Use when only backend/frontend app code changed:

```bash
cd backend/deploy/scripts
./deploy_app_only.sh ../vm-terraform/terraform.tfvars
```

What it does:

- Computes backend/frontend hashes locally
- Compares against VM state (`/opt/azure-resilience-iq/.deploy-hashes.env`)
- Syncs only changed app folders
- Reinstalls backend deps/restarts service only when backend changed
- Rebuilds frontend/reloads nginx only when frontend changed

---

## Local Development with Direct Azure AI Foundry SDK

The backend LLM runtime uses direct Azure AI Foundry SDK calls.

### 1. Configure `backend/config/app_config.yaml`

```yaml
llm:
  enabled: true

ai_agent:
  foundry_project_endpoint: "https://<your-foundry-resource>.services.ai.azure.com/api/projects/<project-name>"
  openai_api_version: "2024-10-21"
  reasoning_model: "gpt-4.1"
  embedding_model: "text-embedding-3-small"
  chat_agent_reference: "chat-agent"
  resilience_agent_reference: "resilience-agent"
  annotations_agent_reference: "annotations-agent"
  terraform_agent_reference: "terraform-compiler-agent"
  run_timeout_seconds: 120
  poll_interval_seconds: 1.5
```

### 2. Configure `backend/.env`

```env
AZURE_SEARCH_ENDPOINT=<your-search-endpoint>
AZURE_SEARCH_ADMIN_KEY=<your-search-admin-key>

AI_FOUNDRY_PROJECT_ENDPOINT=https://<your-foundry-resource>.services.ai.azure.com/api/projects/<project-name>

# Optional Foundry + Agent overrides
AI_FOUNDRY_OPENAI_API_VERSION=2024-10-21
AI_FOUNDRY_REASONING_MODEL=gpt-4.1
AI_FOUNDRY_EMBEDDING_MODEL=text-embedding-3-small
AI_FOUNDRY_CHAT_AGENT_REFERENCE=chat-agent
AI_FOUNDRY_RESILIENCE_AGENT_REFERENCE=resilience-agent
AI_FOUNDRY_ANNOTATIONS_AGENT_REFERENCE=annotations-agent
AI_FOUNDRY_TERRAFORM_AGENT_REFERENCE=terraform-compiler-agent
```

`AI_FOUNDRY_PROJECT_ENDPOINT` is required to call Foundry runtime routes.
Ensure your principal has permissions on the Foundry project/resource.

## Workflow: Collector → Resilience Evaluation → LLM Annotation → API

### Step 1: Run the Azure Resource Graph collector

```bash
cd backend
python -m app.collector.run --subscription-id <your-subscription-id>
```

Generates:

- `backend/data/{subscription-id}/resources.json`
- `backend/data/{subscription-id}/edges.json`

### Step 2: Run resilience evaluations

```bash
cd backend
python -m app.resilience.run --subscription-id <your-subscription-id>
```

Saves `backend/data/{subscription-id}/resilience_evaluations.json`.

### Step 3: Run the LLM annotator (optional)

```bash
cd backend
python -m app.llm.run --subscription-id <your-subscription-id>
```

Saves `backend/data/{subscription-id}/llm_annotations.json`.

### Step 4: Start API server

```bash
cd backend
uvicorn app.main:app --reload
```

### Step 5: Access API

```bash
curl "http://localhost:8000/api/subscriptions/<your-subscription-id>/graph"
curl "http://localhost:8000/api/resilience/evaluate/<your-subscription-id>"
curl "http://localhost:8000/api/<your-subscription-id>/recommendations"
```

## Notes

- `.env` is gitignored. Never commit credentials.
- `.env.sample` is tracked. Use it as a template.
- APRL deterministic evaluation remains the source of truth.
- Foundry portal "Data + indexes" may not display external Azure AI Search index attachments used by agent tools; use provisioning/diagnostic output for verification.
