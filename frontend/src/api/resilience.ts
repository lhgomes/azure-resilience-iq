/**
 * Resiliency API Service
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

export interface ResiliencyCheck {
  recommendation_id: string;
  resilience_check_id: string;  // Unique ID based on resource_id + recommendation_id
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
  validation_source: string;  // Top-level validation source: 'APRL', 'Heuristic', 'LLM', 'ZoneRecommendation', etc.
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
  checks: ResiliencyCheck[];
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

export interface ResiliencyCheckMetrics {
  total_checks: number;
  passed_checks: number;
  failed_checks: number;
  pass_percentage: number;
}

export interface ResiliencySummary {
  [resourceId: string]: ResiliencyCheckMetrics;
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
export async function getResiliencySummary(
  subscriptionId: string
): Promise<ResiliencySummary> {
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
  resilience_check_id: string;  // New primary ID
  check_uuid?: string;  // Deprecated, kept for backward compatibility
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
  resilienceCheckId: string
): Promise<{ deleted: boolean; resilience_check_id: string }> {
  return apiJson(`/api/resilience/${encodeURIComponent(subscriptionId)}/overrides`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ resilience_check_id: resilienceCheckId }),
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

/**
 * Zonal Resiliency Types and APIs
 */

export type DeploymentPattern = 'zone_redundant' | 'multi_zone' | 'single_zone' | 'not_applicable' | 'unknown';

export interface ZonalData {
  zones_used: string[];
  is_zone_redundant: boolean;
  zone_count: number;
  meets_3az_requirement: boolean;
  deployment_pattern: DeploymentPattern;
  recommendation: string;
}

export interface ResourceZonalAnalysis {
  resource_id: string;
  resource_name: string;
  resource_type: string;
  location: string;
  zonal_data: ZonalData;
}

export interface ZonalResiliencySummary {
  total_resources: number;
  zone_redundant_resources: number;
  multi_zone_resources: number;
  single_zone_resources: number;
  not_applicable_resources: number;
  unknown_zone_resources: number;
  compliant_3az_resources: number;
  overall_3az_compliant: boolean;
  zonal_resilience_score: number;
  compliance_percentage: number;
  regional_analysis?: {
    total_regions: number;
    zone_enabled_regions_count: number;
    non_zone_regions_count: number;
    resources_in_zone_regions: number;
    resources_in_non_zone_regions: number;
    regions: string[];
    zone_enabled_regions: string[];
    non_zone_regions: string[];
  };
}

export interface ZonalResiliencyResponse {
  subscription_id: string;
  analysis_timestamp: string;
  summary?: ZonalResiliencySummary;  // Optional - will be calculated frontend-side from resources
  resources: ResourceZonalAnalysis[];
}

/**
 * Get zonal resilience analysis for a subscription
 */
export async function getZonalResiliency(
  subscriptionId: string
): Promise<ZonalResiliencyResponse> {
  return apiJson(
    `/api/subscriptions/${encodeURIComponent(subscriptionId)}/zonal-resilience`
  );
}

/**
 * Get deployment pattern display name
 */
export function getDeploymentPatternLabel(pattern: DeploymentPattern): string {
  const labels: Record<DeploymentPattern, string> = {
    zone_redundant: 'Zone Redundant',
    multi_zone: 'Multi-Zone',
    single_zone: 'Single Zone',
    not_applicable: 'Not Applicable',
    unknown: 'Unknown',
  };
  return labels[pattern] || pattern;
}

/**
 * Get deployment pattern color for UI
 */
export function getDeploymentPatternColor(pattern: DeploymentPattern): string {
  const colors: Record<DeploymentPattern, string> = {
    zone_redundant: '#10b981',  // Green
    multi_zone: '#3b82f6',      // Blue
    single_zone: '#f59e0b',     // Amber
    not_applicable: '#9ca3af',  // Gray
    unknown: '#6b7280',         // Dark gray
  };
  return colors[pattern] || '#9ca3af';
}

/**
 * Get deployment pattern icon
 */
export function getDeploymentPatternIcon(pattern: DeploymentPattern): string {
  const icons: Record<DeploymentPattern, string> = {
    zone_redundant: '✓',   // Checkmark
    multi_zone: '◉◉◉',     // Multiple circles
    single_zone: '◉',      // Single circle
    not_applicable: '—',   // Dash
    unknown: '?',          // Question mark
  };
  return icons[pattern] || '?';
}

