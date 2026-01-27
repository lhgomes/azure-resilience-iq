/**
 * Shared resilience score calculation utilities
 * 
 * This is the SINGLE SOURCE OF TRUTH for resilience score calculations.
 * All components that need scores should import and use these functions.
 */

export interface ResiliencyWeights {
  categoryWeights: Record<string, number>;
  impactWeights: Record<string, number>;
}

export interface ResiliencyCheck {
  status: "pass" | "fail";
  category: string;
  impact: "High" | "Medium" | "Low";
  [key: string]: any;
}

/**
 * Calculate resilience score for a single resource
 * 
 * Uses three-factor formula: element_weight × category_weight × impact_weight
 * 
 * @param checks - Array of resilience checks for the resource
 * @param elementWeight - Criticality weight from graph annotations (default: 1.0)
 * @param weights - Category and impact weights from backend
 * @returns Score between 0.0 and 1.0
 */
export function calculateResiliencyScore(
  checks: ResiliencyCheck[],
  elementWeight: number,
  weights: ResiliencyWeights
): number {
  if (!checks || checks.length === 0) return 0;

  let totalWeight = 0;
  let passedWeight = 0;

  checks.forEach((check) => {
    const categoryWeight = weights.categoryWeights[check.category] ?? 0.05;
    const impactWeight = weights.impactWeights[check.impact] ?? 0.1;
    const checkWeight = elementWeight * categoryWeight * impactWeight;

    totalWeight += checkWeight;
    if (check.status === "pass") {
      passedWeight += checkWeight;
    }
  });

  return totalWeight > 0 ? passedWeight / totalWeight : 0;
}

/**
 * Get element weight from graph node annotations
 * 
 * @param nodeId - Resource ID to look up
 * @param annotationMap - Map of node ID to LLM annotations
 * @returns Element criticality weight (default: 1.0)
 */
export function getElementWeight(
  nodeId: string,
  annotationMap: Map<string, any>
): number {
  const annotation = annotationMap.get(nodeId.toLowerCase());
  return annotation?.criticality_weight ?? 1.0;
}

/**
 * Default weights (matches backend config defaults)
 */
export const DEFAULT_WEIGHTS: ResiliencyWeights = {
  categoryWeights: {
    HighAvailability: 0.30,
    DisasterRecovery: 0.20,
    Scalability: 0.20,
    MonitoringAndAlerting: 0.15,
    Security: 0.10,
    OtherBestPractices: 0.05,
  },
  impactWeights: {
    High: 0.6,
    Medium: 0.3,
    Low: 0.1,
  },
};
