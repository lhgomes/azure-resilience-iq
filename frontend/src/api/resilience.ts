/**
 * Resilience API Service
 * 
 * Provides access to resilience scoring and evaluation data from the backend.
 */

// Internal API helper
async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let bodyText: string | undefined;
    try {
      bodyText = await res.text();
    } catch {
      bodyText = undefined;
    }
    const suffix = bodyText ? `: ${bodyText}` : "";
    throw new Error(`Request failed (${res.status})${suffix}`);
  }
  return (await res.json()) as T;
}

export interface CategoryScore {
  name: string;
  score: number;  // 0.0-1.0
  weight: number;
  passed_weight: number;
  total_weight: number;
  checks_count: number;
  passed_count: number;
}

export interface ResilienceCheck {
  recommendation_id: string;
  description: string;
  category: string;
  impact: 'High' | 'Medium' | 'Low';
  long_description: string;
  potential_benefits: string;
  learn_more: {
    name: string;
    url: string;
    llm_reasoning?: string;
  };
  criticality_weight: number;
  status: 'pass' | 'fail' | 'pending';
  validation_source: 'APRL' | 'Heuristic' | 'LLM' | 'PendingReview';
  impact_weight: number;
  contribution_percent: number;
  is_critical: boolean;
}

export interface ResourceEvaluation {
  resource_id: string;
  resource_type: string;
  resource_name: string;
  component_score: number;  // 0.0-1.0
  component_weight: number;
  categories: CategoryScore[];
  checks: ResilienceCheck[];
  total_checks: number;
  passed_checks: number;
  failed_checks: number;
}

export interface CategoryBreakdown {
  [category: string]: {
    score: number;  // 0.0-1.0
    weight: number;
    checks_count: number;
    passed_count: number;
  };
}

export interface WorkloadScoring {
  workload_score: number;  // 0.0-1.0 (overall resilience score)
  category_breakdown: CategoryBreakdown;
  scoring_timestamp: string;
}

export interface SubscriptionEvaluationResponse {
  subscription_id: string;
  summary: {
    total_resources_evaluated: number;
    total_checks: number;
    total_failed_checks: number;
    total_passed_checks: number;
    average_category_scores: Record<string, number>;
  };
  evaluations: Record<string, ResourceEvaluation>;
  // Scoring data
  workload_score?: number;
  category_breakdown?: CategoryBreakdown;
  scoring_timestamp?: string;
}

export interface ResilienceCheckMetrics {
  total_checks: number;
  passed_checks: number;
  failed_checks: number;
  pass_percentage: number;
}

export interface ResilienceSummary {
  [resourceId: string]: ResilienceCheckMetrics;
}

/**
 * Get complete resilience evaluation for a subscription including scores
 */
export async function getSubscriptionEvaluation(
  subscriptionId: string
): Promise<SubscriptionEvaluationResponse> {
  return apiJson(`/api/resilience/evaluate/${encodeURIComponent(subscriptionId)}`);
}

/**
 * Get resilience evaluation for a specific resource
 */
export async function getResourceEvaluation(
  subscriptionId: string,
  resourceId: string
): Promise<ResourceEvaluation> {
  const encodedResourceId = encodeURIComponent(resourceId);
  return apiJson(
    `/api/resilience/evaluate/${encodeURIComponent(subscriptionId)}/resource/${encodedResourceId}`
  );
}

/**
 * Get resilience summary (quick metrics for all resources)
 */
export async function getResilienceSummary(
  subscriptionId: string
): Promise<ResilienceSummary> {
  return apiJson(`/api/resilience/evaluate/${encodeURIComponent(subscriptionId)}/summary`);
}

/**
 * Format score as percentage string
 */
export function formatScore(score: number): string {
  return `${(score * 100).toFixed(1)}%`;
}

/**
 * Get color based on score (for UI)
 */
export function getScoreColor(score: number): string {
  if (score >= 0.8) return '#22c55e'; // green
  if (score >= 0.6) return '#eab308'; // yellow
  if (score >= 0.4) return '#f97316'; // orange
  return '#ef4444'; // red
}

/**
 * Get score status label
 */
export function getScoreStatus(score: number): string {
  if (score >= 0.8) return 'Excellent';
  if (score >= 0.6) return 'Good';
  if (score >= 0.4) return 'Fair';
  return 'Needs Improvement';
}

/**
 * Override interfaces
 */
export interface OverrideData {
  check_uuid: string;
  resource_id: string;
  recommendation_id: string;
  status: 'pass' | 'fail';
  overridden_at: string;
  overridden_by: string;
}

export interface OverridesResponse {
  subscription_id: string;
  overrides: Record<string, OverrideData>;
  count: number;
}

/**
 * Get all overrides for a subscription
 */
export async function getOverrides(subscriptionId: string): Promise<OverridesResponse> {
  return apiJson(`/api/resilience/${encodeURIComponent(subscriptionId)}/overrides`);
}

/**
 * Create or update an override
 */
export async function saveOverride(
  subscriptionId: string,
  resourceId: string,
  recommendationId: string,
  newStatus: 'pass' | 'fail',
  userIdentifier: string = 'user'
): Promise<OverrideData> {
  return apiJson(`/api/resilience/${encodeURIComponent(subscriptionId)}/overrides`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      resource_id: resourceId,
      recommendation_id: recommendationId,
      new_status: newStatus,
      user_identifier: userIdentifier,
    }),
  });
}

/**
 * Delete an override
 */
export async function deleteOverride(
  subscriptionId: string,
  checkUuid: string
): Promise<{ deleted: boolean; check_uuid: string }> {
  return apiJson(`/api/resilience/${encodeURIComponent(subscriptionId)}/overrides`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ check_uuid: checkUuid }),
  });
}

/**
 * Get override for a specific check
 */
export async function getCheckOverride(
  subscriptionId: string,
  resourceId: string,
  recommendationId: string
): Promise<OverrideData | null> {
  const params = new URLSearchParams({
    resource_id: resourceId,
    recommendation_id: recommendationId,
  });
  return apiJson(
    `/api/resilience/${encodeURIComponent(subscriptionId)}/overrides/check?${params}`
  );
}

