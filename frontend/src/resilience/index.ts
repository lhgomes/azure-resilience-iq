/**
 * Resiliency Scoring Frontend Integration
 * 
 * This module provides complete frontend integration for the backend
 * resilience scoring system.
 * 
 * Components:
 * - ScoreCircle: Circular score indicator (0-100%)
 * - CategoryBreakdownView: Category-level scores with bars
 * - ResiliencyScoreDashboard: Complete scoring dashboard
 * 
 * API Services:
 * - getSubscriptionEvaluation: Get full evaluation with scores
 * - getResourceEvaluation: Get resource-specific evaluation
 * - getResiliencySummary: Get quick metrics
 * - getCategoryWeights: Get category configuration
 * 
 * Usage:
 * ```tsx
 * import { ResiliencyScoreDashboard } from './resilience';
 * 
 * <ResiliencyScoreDashboard subscriptionId="your-subscription-id" />
 * ```
 */

export { default as ScoreCircle } from '../components/ScoreCircle';
export { default as CategoryBreakdownView } from '../components/CategoryBreakdownView';
export { default as ResiliencyScoreDashboard } from '../pages/ResilienceScoreDashboard';

export * from '../api/resilience';
