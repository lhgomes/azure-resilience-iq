# Resilience Analysis Module

## Overview

This module implements resilience evaluation and scoring based on the business logic from the resiliency-scorecard project. It evaluates Azure workload components against APRL v2 (Azure Proactive Resiliency Library) recommendations and calculates resilience scores.

## Architecture

### Complete Workload Analysis Pipeline

```
1. Collect (existing)
   └─ Graph builder discovers Azure resources
   
2. LLM Annotations (existing)
   └─ Annotator adds insights and properties
   
3. Evaluate (NEW - this module)
   └─ ResilienceEvaluator checks components against APRL recommendations
   └─ Organizes results by category (Availability, DR, Monitoring, Security)
   
4. Score (NEW - this module)
   └─ ResilienceScorer calculates component-level scores
   └─ Aggregates to workload-level resilience score
   
5. Frontend Integration
   └─ Returns scores, recommendations, and breakdowns for UI
```

## Components

### 1. ResilienceEvaluator (`evaluator.py`)

Evaluates Azure resources against APRL recommendations.

**Key Methods:**
- `evaluate_workload(workload_name, components)` - Main entry point
- `_evaluate_components_of_type(comp_type, components)` - Evaluates components of a specific resource type
- `_evaluate_recommendation(comp_type, aprl_guid, components)` - Evaluates a single APRL rule
- `_load_kql_for_recommendation()` - Loads KQL queries from APRL directory

**Flow:**
1. Groups components by resource type
2. Loads APRL rules for each type from YAML files
3. For each rule: determines if components pass or fail
4. Organizes results by category (High Availability, Disaster Recovery, etc.)
5. Returns structured evaluation output

**Output:**
```yaml
workload_name: "my-workload"
evaluation_timestamp: "2026-01-12T..."
components:
  - id: "/subscriptions/.../resource1"
    name: "web-vm-01"
    type: "Microsoft.Compute/virtualMachines"
    categories:
      "High Availability":
        passed: ["ha-01", "ha-03"]
        failed: ["ha-02"]
      "Disaster Recovery":
        passed: ["dr-01"]
        failed: ["dr-02", "dr-03"]
```

### 2. ResilienceScorer (`scorer.py`)

Calculates resilience scores from evaluation results.

**Key Methods:**
- `score_workload(evaluation_results)` - Calculates all scores
- `_score_component(component)` - Component-level scoring
- `_aggregate_component_scores()` - Workload-level aggregation
- `_calculate_category_breakdown()` - Category-level statistics

**Scoring Formula:**

```
Component Score = Σ(Category Weight × Category Score)
where:
  Category Score = Passed Checks / Total Checks for that category

Workload Score = Σ(Component Weight × Component Score) / Σ(Component Weights)
```

**Default Category Weights:**
- High Availability: 0.4 (40%)
- Disaster Recovery: 0.3 (30%)
- Monitoring and Alerting: 0.2 (20%)
- Security: 0.1 (10%)

**Output:**
```yaml
workload_name: "my-workload"
workload_score: 0.75  # 75% resilience
category_scores:
  "High Availability":
    score: 0.80
    passed: 4
    failed: 1
    total: 5
  "Disaster Recovery":
    score: 0.67
    passed: 2
    failed: 1
    total: 3
component_scores:
  - id: "/subscriptions/.../resource1"
    score: 0.78
    pass_rate: 0.78
    categories:
      "High Availability":
        score: 0.80
        passed: 4
        failed: 1
```

### 3. ResiliencePipeline (`pipeline.py`)

Orchestrates the complete evaluation and scoring workflow.

**Key Methods:**
- `run(workload_name, components)` - Executes complete pipeline
- `_generate_recommendations()` - Creates actionable insights
- `_load_category_weights()` - Loads weighting configuration

**Output:**
```json
{
  "workload_name": "my-workload",
  "evaluation": { ... },
  "scoring": { ... },
  "recommendations": [
    {
      "category": "High Availability",
      "priority": 0.6,
      "message": "Improve High Availability: 2 checks failing",
      "score": 0.75,
      "failing_checks": 2
    }
  ]
}
```

## Integration Guide

### 1. Basic Integration

```python
from app.resilience.pipeline import ResiliencePipeline
from app.models import WorkloadComponent

# Initialize pipeline
pipeline = ResiliencePipeline(
    aprl_root="./backend/aprl",
    rules_dir="./config/resiliency_rules",
    category_weights_path="./config/category_weights.yaml",
)

# Create components (from your graph/LLM)
components = [
    WorkloadComponent(
        id="/subscriptions/sub1/resourceGroups/rg1/providers/.../vm1",
        name="web-server",
        resource_type="Microsoft.Compute/virtualMachines",
        properties={
            "has_redundancy": True,
            "monitoring_enabled": True,
            "has_backup": False,
        }
    ),
    # ... more components
]

# Run analysis
results = pipeline.run(
    workload_name="production-app",
    components=components,
)

# Access results
workload_score = results["scoring"]["workload_score"]
recommendations = results["recommendations"]
```

### 2. Integration with Existing Graph

```python
from app.resilience.integration import analyze_workload_resilience

# After building graph and running LLM annotations
resilience_results = await analyze_workload_resilience(
    workload_graph=annotated_graph,
    workload_name="my-workload",
)
```

### 3. Frontend Integration

```python
# In your FastAPI endpoint
@app.post("/workloads/{workload_id}/analyze")
async def analyze_workload(workload_id: str):
    graph = await load_workload_graph(workload_id)
    results = await analyze_workload_resilience(graph, workload_id)
    
    return {
        "resilience_score": results["scoring"]["workload_score"],
        "category_scores": results["scoring"]["category_scores"],
        "recommendations": results["recommendations"],
        "components": results["scoring"]["component_scores"],
    }
```

## Configuration

### Category Weights (`category_weights.yaml`)

```yaml
"High Availability": 0.40
"Disaster Recovery": 0.30
"Monitoring and Alerting": 0.20
"Security": 0.10
```

### Component Properties

The evaluation uses component properties for heuristic evaluation:
- `has_redundancy` - Multi-region/zone deployment
- `has_backup` - Backup configured
- `monitoring_enabled` - Monitoring and alerting active

These can be enriched from:
1. Graph builder discovery
2. LLM annotations
3. Azure Resource Graph queries
4. KQL queries

## Evaluation Logic

### Current Implementation (Heuristic-based)

The module includes a heuristic evaluator that uses component properties:

```python
def _evaluate_component_by_heuristic(component, aprl_guid):
    properties = component.properties
    
    if aprl_guid.startswith("ha-"):
        return properties.get("has_redundancy", False)
    if aprl_guid.startswith("dr-"):
        return properties.get("has_backup", False)
    # ... more rules
```

### Production Implementation (KQL-based)

To use actual APRL KQL queries:

1. Load KQL from APRL directory:
   ```python
   kql = self._load_kql_for_recommendation(comp_type, aprl_guid)
   ```

2. Execute against Azure Monitor/Resource Graph:
   ```python
   results = execute_kql_query(kql, subscription_id, resource_ids)
   ```

3. Map results to components:
   ```python
   for resource_id, passed in results.items():
       component_results[resource_id] = passed
   ```

## Extension Points

### 1. Custom Evaluation Logic

Override `_evaluate_recommendation()` to implement custom logic:

```python
class CustomEvaluator(ResilienceEvaluator):
    def _evaluate_recommendation(self, comp_type, aprl_guid, components):
        # Custom implementation
        return self._evaluate_with_azure_api(aprl_guid, components)
```

### 2. Custom Scoring

Implement different scoring formulas:

```python
class CustomScorer(ResilienceScorer):
    def _aggregate_component_scores(self, component_scores):
        # Custom aggregation logic
        return max(cs["score"] for cs in component_scores)
```

### 3. Custom Recommendations

Add domain-specific recommendation generation:

```python
def _generate_recommendations(self, scoring_results):
    recommendations = super()._generate_recommendations(scoring_results)
    # Add custom recommendations
    return recommendations
```

## Data Models

### WorkloadComponent

```python
@dataclass
class WorkloadComponent:
    id: str                          # Azure resource ID
    name: str                        # Resource name
    resource_type: str               # e.g., "Microsoft.Compute/virtualMachines"
    properties: Dict[str, Any]      # Resilience-related properties
```

### Evaluation Result

```python
{
    "id": "resource-id",
    "name": "resource-name",
    "type": "resource-type",
    "categories": {
        "category-name": {
            "passed": ["check-1", "check-2"],
            "failed": ["check-3"],
        }
    }
}
```

### Score Result

```python
{
    "id": "resource-id",
    "score": 0.75,                  # 0.0 to 1.0
    "pass_rate": 0.75,              # proportion of passed checks
    "total_checks": 4,
    "total_passed": 3,
    "categories": {
        "category-name": {
            "score": 0.80,
            "passed": 2,
            "failed": 0,
            "total": 2,
            "weight": 0.40,
        }
    }
}
```

## APRL Integration

### Directory Structure Expected

```
backend/aprl/
├── <ResourceType1>/
│   ├── kql/
│   │   ├── <guid1>.kql
│   │   ├── <guid2>.kql
│   └── ...
├── <ResourceType2>/
│   └── ...
└── ...
```

### Rules File Format

```yaml
- id: "rule-1"
  aprlGuid: "ha-01"
  category: "High Availability"
  description: "Resource has redundancy"
  automationAvailable: true
  severity: "High"

- id: "rule-2"
  aprlGuid: "dr-02"
  category: "Disaster Recovery"
  description: "Backup is configured"
  automationAvailable: true
  severity: "Critical"
```

## Testing

```python
def test_evaluation():
    evaluator = ResilienceEvaluator(
        aprl_root="./test/aprl",
        rules_dir="./test/rules",
    )
    
    components = [
        WorkloadComponent(
            id="test-vm",
            name="test",
            resource_type="Microsoft.Compute/virtualMachines",
            properties={"has_redundancy": True},
        )
    ]
    
    results = evaluator.evaluate_workload("test", components)
    assert results["workload_name"] == "test"
    assert len(results["components"]) == 1

def test_scoring():
    scorer = ResilienceScorer(
        category_weights={
            "Availability": 0.6,
            "Recovery": 0.4,
        }
    )
    
    eval_results = {
        "workload_name": "test",
        "components": [
            {
                "id": "comp-1",
                "categories": {
                    "Availability": {"passed": [1, 2], "failed": []},
                    "Recovery": {"passed": [1], "failed": [2]},
                }
            }
        ]
    }
    
    scores = scorer.score_workload(eval_results)
    assert 0.0 <= scores["workload_score"] <= 1.0
```

## Troubleshooting

### Missing APRL Rules

```
No rules found for Microsoft.Compute/virtualMachines
```

Solution: Ensure APRL v2 is cloned to `backend/aprl` and rules are generated in `config/resiliency_rules/`.

### Zero Category Weights

```
ValueError: Category weights must sum to non-zero value
```

Solution: Ensure `category_weights.yaml` has non-zero weights that sum to a positive value.

### Mismatched Component IDs

Ensure component IDs from the graph match Azure resource ID format:
```
/subscriptions/{subId}/resourceGroups/{rgName}/providers/Microsoft.Compute/virtualMachines/{vmName}
```

## References

- [Azure Proactive Resiliency Library v2](https://github.com/Azure/Azure-Proactive-Resiliency-Library-v2)
- [Resiliency Scorecard Project](https://github.com/msalem2011/resiliency-scorecard)
- [APRL Category Definitions](https://aka.ms/aprl)
