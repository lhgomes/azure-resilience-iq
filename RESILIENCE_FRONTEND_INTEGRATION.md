# Frontend Resilience Scoring Integration - Complete

## Overview
Complete frontend integration for the hierarchical resilience scoring system implemented in the backend.

## Implementation Status

### ✅ Step 1: Backend Scoring Calculation (COMPLETE)
- **File**: `backend/app/resilience/scorer.py`
- **Features**:
  - Hierarchical impact-weighted scoring (Check → Category → Resource → Workload)
  - Impact weights: High (0.5), Medium (0.3), Low (0.1)
  - Category-level aggregation with configurable weights
  - Resource-level component scores (0.0-1.0)
  - Workload-level overall score (0.0-1.0)
  - Contribution percentage now calculated per resource (not per category)

### ✅ Step 2: Backend API Integration (COMPLETE)
- **File**: `backend/app/routes/resilience.py`
- **Endpoints**:
  - `GET /api/resilience/evaluate/{subscription_id}` - Full evaluation with scores
  - `GET /api/resilience/evaluate/{subscription_id}/resource/{resource_id}` - Resource details
  - `GET /api/resilience/evaluate/{subscription_id}/summary` - Quick metrics
  - `GET /api/resilience/categories` - Category weights
- **Data Structure**: `resilience_evaluations.json` includes:
  - `workload_score`: Overall score (0.0-1.0)
  - `category_breakdown`: Per-category scores
  - `component_score`: Per-resource scores
  - `checks`: Enriched with scoring metadata

### ✅ Step 3: Frontend API Service (COMPLETE)
- **File**: `frontend/src/api/resilience.ts`
- **Functions**:
  - `getSubscriptionEvaluation()` - Full scoring data
  - `getResourceEvaluation()` - Resource-specific data
  - `getResilienceSummary()` - Quick metrics
  - `getCategoryWeights()` - Configuration
  - `formatScore()`, `getScoreColor()`, `getScoreStatus()` - Utilities
- **TypeScript Types**:
  - `WorkloadScoring` - Overall scores
  - `CategoryBreakdown` - Category-level data
  - `ResourceEvaluation` - Resource-level data
  - `ResilienceCheck` - Check-level data

### ✅ Step 4: Frontend UI Components (COMPLETE)
- **ScoreCircle** (`frontend/src/components/ScoreCircle.tsx`)
  - Circular progress indicator showing 0-100% score
  - Color-coded: Green (≥80%), Yellow (≥60%), Orange (≥40%), Red (<40%)
  - Displays status label: Excellent, Good, Fair, Needs Improvement
  - Configurable size and labels

- **CategoryBreakdownView** (`frontend/src/components/CategoryBreakdownView.tsx`)
  - Visual bar chart of category scores
  - Shows passed/total checks per category
  - Optional weight display
  - Sorted by score (highest first)

### ✅ Step 5: Dashboard Integration (COMPLETE)
- **ResilienceScoreDashboard** (`frontend/src/pages/ResilienceScoreDashboard.tsx`)
  - Complete scoring dashboard page
  - Overall workload score with status
  - Category breakdown visualization
  - Resource summary statistics
  - Error handling and loading states
  - Helpful messages when data is missing

## Data Flow

```
Backend Evaluation
      ↓
resilience_evaluations.json
      ↓
FastAPI Endpoints (/api/resilience/*)
      ↓
Frontend API Service (resilience.ts)
      ↓
React Components (ScoreCircle, CategoryBreakdownView)
      ↓
Dashboard Page (ResilienceScoreDashboard)
```

## Usage

### 1. Run Backend Evaluation
```bash
cd backend
python -m app.resilience.run --subscription-id <subscription-id>
```

### 2. Start Backend Server
```bash
cd backend
uvicorn app.main:app --reload
```

### 3. Use in Frontend
```tsx
import { ResilienceScoreDashboard } from './resilience';

function App() {
  return (
    <ResilienceScoreDashboard subscriptionId="your-subscription-id" />
  );
}
```

## Component Examples

### Score Circle
```tsx
import { ScoreCircle } from './resilience';

<ScoreCircle 
  score={0.75}  // 75%
  size={140} 
  showLabel={true} 
  label="Resilience Score" 
/>
```

### Category Breakdown
```tsx
import { CategoryBreakdownView } from './resilience';

<CategoryBreakdownView 
  breakdown={categoryBreakdown} 
  showWeights={true} 
/>
```

### Full Dashboard
```tsx
import { ResilienceScoreDashboard } from './resilience';

<ResilienceScoreDashboard subscriptionId="abc-123" />
```

## API Response Example

```json
{
  "subscription_id": "abc-123",
  "workload_score": 0.75,
  "category_breakdown": {
    "HighAvailability": {
      "score": 0.80,
      "weight": 0.35,
      "checks_count": 9,
      "passed_count": 7
    },
    "DisasterRecovery": {
      "score": 0.65,
      "weight": 0.30,
      "checks_count": 5,
      "passed_count": 3
    }
  },
  "evaluations": {
    "/subscriptions/.../vm1": {
      "component_score": 0.78,
      "component_weight": 57.14,
      "categories": [...],
      "checks": [...]
    }
  }
}
```

## Scoring Model

### Hierarchical Structure
```
Workload Score (0-100%)
  └── Weighted avg of Resource Scores
      └── Resource/Component Score (0-100%)
          └── Weighted avg of Category Scores
              └── Category Score (0-100%)
                  └── (Passed Check Weights) / (Total Check Weights)
                      └── Check
                          ├── High Impact: 0.5
                          ├── Medium Impact: 0.3
                          └── Low Impact: 0.1
```

### Contribution Percentage
- **Fixed**: Now calculated per resource (not per category)
- **Formula**: `contribution_percent = (check_impact_weight / resource_total_weight) × 100`
- **Example**: High-impact check (0.5) in resource with total weight 7.4 = 6.76%

## Color Scheme
- **Green (#22c55e)**: Score ≥ 80% (Excellent)
- **Yellow (#eab308)**: Score ≥ 60% (Good)
- **Orange (#f97316)**: Score ≥ 40% (Fair)
- **Red (#ef4444)**: Score < 40% (Needs Improvement)

## Files Created

### Frontend
1. `frontend/src/api/resilience.ts` - API service layer
2. `frontend/src/components/ScoreCircle.tsx` - Score visualization
3. `frontend/src/components/CategoryBreakdownView.tsx` - Category scores
4. `frontend/src/pages/ResilienceScoreDashboard.tsx` - Main dashboard
5. `frontend/src/resilience/index.ts` - Module exports

### Documentation
6. `RESILIENCE_FRONTEND_INTEGRATION.md` - This file

## Next Steps (Optional Enhancements)

1. **Add to Main Navigation**: Integrate dashboard into main app routing
2. **Real-time Updates**: Add refresh button or auto-refresh
3. **Filtering**: Filter by category, impact, or status
4. **Drill-down**: Click categories to see specific checks
5. **Export**: Export scores to PDF or Excel
6. **Trend Analysis**: Track scores over time
7. **Alerts**: Notify when scores drop below thresholds

## Testing

### Test Score Display
```bash
# Backend: Run evaluation
cd backend
python -m app.resilience.run --subscription-id bab86631-7bdc-42ec-8760-30baaf61fad1

# Frontend: Check API
curl http://localhost:8000/api/resilience/evaluate/bab86631-7bdc-42ec-8760-30baaf61fad1

# Frontend: View in browser
http://localhost:5173  # Add ResilienceScoreDashboard to your routes
```

## Summary

✅ **All 5 steps completed**:
1. ✅ Backend scoring calculation
2. ✅ Backend API endpoints  
3. ✅ Frontend API service
4. ✅ Frontend UI components
5. ✅ Dashboard integration

The resilience scoring system is now fully integrated from backend to frontend with:
- Hierarchical impact-weighted scoring
- Real-time visualization
- Category breakdowns
- Resource-level scores
- Workload-level overview
