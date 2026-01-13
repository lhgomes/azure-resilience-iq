"""
Hierarchical Impact-Based Resilience Scoring Engine.

SCORING MODEL - Tree Structure with Impact Weights:

1. CHECK LEVEL (Leaf):
   impact_weight = High(0.5) | Medium(0.3) | Low(0.1)
   contribution = impact_weight if status="pass" else 0
   
2. CATEGORY LEVEL:
   category_score = Σ(weight of passed checks) / Σ(weight of all checks)
   range: 0.0-1.0
   
3. RESOURCE LEVEL (component_score):
   component_score = Σ(category_weight × category_score)
   range: 0.0-1.0 (normalized if category_weights sum to 1.0)
   
4. WORKLOAD LEVEL:
   workload_score = Σ(component_weight × component_score) / Σ(component_weights)
   range: 0.0-1.0 (normalized if component_weights sum to 1.0)

ALL SCALES: 0.0-1.0 internally, display as 0-100%
ALL WEIGHTS: Sum must not exceed 1.0

UI Impact Clarity:
- User sees: "Fixing this High impact check will increase category score by X%"
- Each resource shows which categories need work
- Workload score shows which resources have highest impact on total score
"""

from typing import Dict, Any, List
import yaml
import os
from datetime import datetime, timezone


class ResilienceScorer:
    """
    Hierarchical Impact-Based Resilience Scoring Engine.
    
    Uses weighted checks at category level and hierarchical aggregation
    to produce 0.0-1.0 scores at resource and workload levels.
    """
    
    # Impact weights - assigned to each check based on severity
    IMPACT_WEIGHTS = {
        "High": 0.5,
        "Medium": 0.3,
        "Low": 0.1
    }
    
    def __init__(self, category_weights: Dict[str, float] = None):
        """
        Initialize scorer with weights.
        
        Args:
            category_weights: Map of category name to weight (0.0-1.0)
                If sum > 1.0, will be normalized
        """
        self.category_weights = category_weights or self._load_category_weights()
        self._normalize_weights(self.category_weights, "category")
    
    @staticmethod
    def _load_category_weights() -> Dict[str, float]:
        """Load category weights from config file."""
        try:
            config_path = os.path.join(
                os.path.dirname(__file__), '..', '..', 'config', 'app_config.yaml'
            )
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
            return config.get('category_weights', {})
        except Exception as e:
            print(f"Warning: Could not load config file: {e}")
            return {}
    
    @staticmethod
    def _normalize_weights(weights: Dict[str, float], weight_type: str) -> None:
        """
        Normalize weights to sum to 1.0 if they exceed it.
        
        Args:
            weights: Weight dictionary to normalize
            weight_type: Name of weight type (for logging)
        """
        total = sum(weights.values())
        if total > 1.0:
            factor = 1.0 / total
            for key in weights:
                weights[key] *= factor
            print(f"Normalized {weight_type} weights to sum to 1.0")
    
    def score_workload(self, evaluation_results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Score a workload based on evaluation results.
        
        Uses hierarchical impact-based scoring:
        1. Check level: Individual check results with impact weights
        2. Category level: Weighted aggregation of checks
        3. Resource level (component_score): Aggregation of categories
        4. Workload level: Aggregation of resources
        
        Args:
            evaluation_results: Dict with 'resources' key containing list of resources
        
        Returns:
            Dict with scoring results including:
            - workload_score: Overall workload score (0.0-1.0)
            - resources: Scored resources with component_score, categories, and enriched checks
            - category_breakdown: Average scores for each category across resources
            - timestamp: ISO format timestamp
            
        Note: Checks are enriched with impact_weight, contribution_percent, is_critical
        (no separate checks_detail field - all data merged into checks)
        """
        resources = evaluation_results.get('resources', [])
        
        if not resources:
            return {
                'workload_score': 0.0,
                'resources': [],
                'category_breakdown': {},
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
        
        # Score each resource with impact-weighted checks
        scored_resources = []
        for resource in resources:
            scored_resource = self._score_resource(resource)
            scored_resources.append(scored_resource)
        
        # Calculate workload score as normalized weighted average
        workload_score = self._calculate_workload_score(scored_resources)
        
        # Calculate category breakdown across all resources
        category_breakdown = self._calculate_category_breakdown(scored_resources)
        
        return {
            'workload_score': workload_score,
            'resources': scored_resources,
            'category_breakdown': category_breakdown,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
    
    def _score_resource(self, resource: Dict[str, Any]) -> Dict[str, Any]:
        """
        Score a single resource with hierarchical impact-weighted model.
        
        Process:
        1. Group checks by category
        2. For each category, calculate score as: 
           (sum of passed check weights) / (sum of all check weights)
        3. Combine category scores into component_score using category weights
        
        Args:
            resource: Resource dict with checks and optional component_weight
        
        Returns:
            Resource dict with added scoring info:
            - component_score: 0.0-1.0 score for this resource
            - component_weight: Importance of this resource (normalized ≤1.0)
            - categories: List of scored categories with impact details
            - checks: Enriched with impact_weight, contribution_percent, is_critical
        """
        resource = dict(resource)  # Avoid mutating input
        
        # Initialize component_weight if not present
        if 'component_weight' not in resource:
            resource['component_weight'] = 1.0
        
        checks = resource.get('checks', [])
        if not checks:
            resource['component_score'] = 0.0
            resource['categories'] = []
            resource['checks'] = []
            return resource
        
        # Group checks by category and calculate impact-weighted scores
        categories_data = self._calculate_category_scores(checks)
        
        # Calculate component_score as weighted average of category scores
        component_score = self._calculate_component_score(categories_data)
        
        # Enrich checks with scoring information (merge with original data)
        enriched_checks = self._enrich_checks_with_scoring(checks, categories_data)
        
        resource['component_score'] = component_score
        resource['categories'] = categories_data
        resource['checks'] = enriched_checks
        
        return resource
    
    def _calculate_category_scores(
        self, checks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Calculate impact-weighted scores for each category.
        
        For each category:
        - Only count "pass" and "fail" checks (exclude "pending" checks)
        - Sum the impact weights of all scorable checks
        - Sum the impact weights of passed checks
        - Category score = passed_weight_sum / total_weight_sum
        
        Args:
            checks: List of check dicts with 'category', 'status' ('pass', 'fail', 'pending'), 'impact'
        
        Returns:
            List of category dicts with scores and impact details
        """
        # Group checks by category, excluding pending checks from scoring
        categories_map = {}
        for check in checks:
            # Skip pending checks - they don't count toward score
            if check.get('status') == 'pending':
                continue
            
            category = check.get('category', 'Uncategorized')
            if category not in categories_map:
                categories_map[category] = []
            categories_map[category].append(check)
        
        # Calculate scores for each category
        categories_data = []
        
        for category_name in sorted(categories_map.keys()):
            category_checks = categories_map[category_name]
            
            # Calculate impact-weighted score for this category
            total_weight = 0.0
            passed_weight = 0.0
            
            for check in category_checks:
                impact = check.get('impact', 'Medium')
                impact_weight = self.IMPACT_WEIGHTS.get(impact, 0.3)
                total_weight += impact_weight
                
                if check.get('status') == 'pass':
                    passed_weight += impact_weight
            
            # Category score: weighted pass rate
            category_score = (passed_weight / total_weight) if total_weight > 0 else 0.0
            category_weight = self.category_weights.get(category_name, 0.0)
            
            categories_data.append({
                'name': category_name,
                'score': category_score,  # 0.0-1.0
                'weight': category_weight,  # importance of category
                'passed_weight': passed_weight,  # sum of weights for passed checks
                'total_weight': total_weight,  # sum of all check weights
                'checks_count': len(category_checks),
                'passed_count': sum(1 for c in category_checks if c.get('status') == 'pass')
            })
        
        return categories_data
    
    def _calculate_component_score(self, categories_data: List[Dict[str, Any]]) -> float:
        """
        Calculate resource/component score from category scores.
        
        Formula: component_score = Σ(category_weight × category_score) / Σ(category_weights)
        Normalizes by sum of weights to ensure 0.0-1.0 range.
        
        Args:
            categories_data: List of scored category dicts
        
        Returns:
            Component score (0.0-1.0)
        """
        weighted_sum = 0.0
        weight_sum = 0.0
        
        for category in categories_data:
            category_score = category['score']
            category_weight = category['weight']
            weighted_sum += category_weight * category_score
            weight_sum += category_weight
        
        if weight_sum > 0:
            return weighted_sum / weight_sum
        
        return 0.0
    
    def _enrich_checks_with_scoring(
        self, 
        checks: List[Dict[str, Any]],
        categories_data: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Enrich original checks with scoring information.
        
        Merges APRL check data with scoring metrics:
        - Impact weight (High/Medium/Low)
        - Whether it's a high-impact blocking check
        
        Note: contribution_percent is calculated dynamically in the frontend
        based on the filtered view, not pre-calculated here.
        
        Args:
            checks: Original check list from APRL evaluation
            categories_data: Scored categories with weight info
        
        Returns:
            Enriched check list with all original data plus scoring fields
        """
        enriched_checks = []
        for check in checks:
            # Start with all original check data
            enriched = dict(check)
            
            impact = check.get('impact', 'Medium')
            impact_weight = self.IMPACT_WEIGHTS.get(impact, 0.3)
            
            # Add scoring fields to enriched check
            enriched['impact_weight'] = impact_weight
            enriched['is_critical'] = impact == 'High'  # Flag high-impact checks
            
            enriched_checks.append(enriched)
        
        return enriched_checks
    
    def _calculate_workload_score(self, resources: List[Dict[str, Any]]) -> float:
        """
        Calculate workload score from resource component scores.
        
        Formula: workload_score = Σ(component_weight × component_score) / Σ(component_weights)
        Normalizes by sum of weights to ensure 0.0-1.0 range.
        
        Args:
            resources: List of scored resource dicts
        
        Returns:
            Workload score (0.0-1.0)
        """
        if not resources:
            return 0.0
        
        weighted_sum = 0.0
        weight_sum = 0.0
        
        for resource in resources:
            component_score = resource.get('component_score', 0.0)
            component_weight = resource.get('component_weight', 1.0)
            
            weighted_sum += component_weight * component_score
            weight_sum += component_weight
        
        if weight_sum > 0:
            return weighted_sum / weight_sum
        
        return 0.0
    
    def _calculate_category_breakdown(self, resources: List[Dict[str, Any]]) -> Dict[str, float]:
        """
        Calculate average category scores across all resources.
        
        Provides workload-level view of which categories need work.
        
        Args:
            resources: List of scored resource dicts
        
        Returns:
            Dict mapping category name to average score (0.0-1.0)
        """
        if not resources:
            return {}
        
        category_scores = {}
        category_counts = {}
        
        for resource in resources:
            categories = resource.get('categories', [])
            for category in categories:
                name = category['name']
                score = category['score']
                
                if name not in category_scores:
                    category_scores[name] = 0.0
                    category_counts[name] = 0
                
                category_scores[name] += score
                category_counts[name] += 1
        
        # Calculate averages
        result = {}
        for name, total_score in category_scores.items():
            count = category_counts.get(name, 1)
            result[name] = total_score / count
        
        return result
