"""
Unified Recommendations Service

Combines recommendations from WARA (official) and resilience module (custom)
to provide maximum coverage to users. Intelligently deduplicates while
maintaining source transparency.

Strategy:
1. Load recommendations from both sources
2. Match recommendations using ID mapping and description similarity
3. Deduplicate with source tracking
4. Enrich with data from both sources
5. Return unified list with source indicators
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from collections import defaultdict

from app.logger import get_logger

LOGGER = get_logger(__name__)


@dataclass
class UnifiedRecommendation:
    """Single recommendation with both source data combined."""
    # Core identification
    id: str  # Primary ID for deduplication
    description: str
    category: str
    impact: str
    resource_id: str
    resource_type: str
    resource_name: str
    
    # Source tracking
    sources: List[str]  # e.g., ["WARA", "RESILIENCE_MODULE"]
    source_ids: Dict[str, str]  # Map source name to its ID
    
    # Details from each source
    wara_data: Optional[Dict[str, Any]] = None
    resilience_data: Optional[Dict[str, Any]] = None
    
    # Enriched fields
    potential_benefits: Optional[str] = None
    long_description: Optional[str] = None
    learn_more_links: List[Dict[str, str]] = None
    
    # Confidence and ranking
    confidence_score: float = 1.0  # How sure are we this is a match (for deduped items)
    

class RecommendationMatcher:
    """Matches recommendations across WARA and resilience module."""
    
    # Known ID mappings (from our analysis)
    ID_MAPPING = {
        # WARA recommendationTypeId → Resiliency aprlGuid
        "1670c0af-6536-4cbf-872f-152c91a51a80": "302fda08-ee65-4fbe-a916-6dc0b33169c4",  # Capacity Reservation
        "5f2613df-629f-4b07-9425-2a47ea0dfad3": "273f6b30-68e0-4241-85ea-acf15ffb60bf",  # VMSS Flex
    }
    
    # Reverse mapping
    REVERSE_ID_MAPPING = {v: k for k, v in ID_MAPPING.items()}
    
    @staticmethod
    def get_matching_id(id_value: str) -> Optional[str]:
        """
        Get matching ID from other system if known.
        
        Args:
            id_value: Recommendation ID from either WARA or resilience module
            
        Returns:
            Matching ID from other system, or None if not found
        """
        return RecommendationMatcher.ID_MAPPING.get(id_value) or \
               RecommendationMatcher.REVERSE_ID_MAPPING.get(id_value)
    
    @staticmethod
    def similarity_score(str1: str, str2: str) -> float:
        """Calculate string similarity (0-1)."""
        return SequenceMatcher(None, str1.lower(), str2.lower()).ratio()
    
    @staticmethod
    def should_match(
        wara_rec: Dict[str, Any],
        res_rec: Dict[str, Any],
        similarity_threshold: float = 0.6
    ) -> Tuple[bool, float]:
        """
        Determine if a WARA and resilience recommendation should be matched.
        
        Uses multiple strategies:
        1. ID mapping (known matches)
        2. Description similarity
        3. Category and resource type match
        
        Returns:
            (should_match, confidence_score)
        """
        wara_id = wara_rec.get('recommendationId', '')
        res_id = res_rec.get('recommendation_id', '')
        
        # Strategy 1: Known ID mappings
        if RecommendationMatcher.get_matching_id(wara_id) == res_id:
            return True, 1.0
        
        # Strategy 2: Description similarity
        wara_desc = wara_rec.get('description', '').lower()
        res_desc = res_rec.get('description', '').lower()
        
        # Normalize for comparison
        wara_desc_norm = ' '.join(wara_desc.split())
        res_desc_norm = ' '.join(res_desc.split())
        
        similarity = RecommendationMatcher.similarity_score(wara_desc_norm, res_desc_norm)
        
        # Strategy 3: Check category and resource type match
        wara_category = wara_rec.get('category', '').lower()
        res_category = res_rec.get('category', '').lower()
        
        wara_type = wara_rec.get('type', '').lower()
        res_type = res_rec.get('resource_type', '').lower()
        
        # Same category and type is a good indicator
        same_category = wara_category == res_category
        same_type = wara_type == res_type
        
        # Confidence increases if multiple attributes match
        confidence = similarity
        if same_category:
            confidence += 0.2
        if same_type:
            confidence += 0.2
        
        confidence = min(1.0, confidence)  # Cap at 1.0
        
        # Match if high confidence
        matches = confidence >= similarity_threshold
        
        return matches, confidence


class UnifiedRecommendationsService:
    """Service for loading and merging recommendations from both sources."""
    
    def __init__(self, data_dir: str = "data"):
        """Initialize service."""
        self.data_dir = Path(data_dir)
        self.matcher = RecommendationMatcher()
    
    def get_unified_recommendations(
        self,
        subscription_id: str,
        include_sources: List[str] = None
    ) -> Dict[str, Any]:
        """
        Get unified recommendations from both sources.
        
        Args:
            subscription_id: Azure subscription ID
            include_sources: Which sources to include (default: both)
                            Options: ['WARA', 'RESILIENCE_MODULE']
                            
        Returns:
            {
                "recommendations": [...],  # Deduplicated unified list
                "total_count": int,
                "by_source": {...},        # Breakdown by source
                "deduplication_info": {...}
            }
        """
        if not include_sources:
            include_sources = ['WARA', 'RESILIENCE_MODULE']
        
        # Load recommendations from both sources
        wara_recs = []
        resilience_recs = []
        
        if 'WARA' in include_sources:
            try:
                wara_recs = self._load_wara_recommendations(subscription_id)
                LOGGER.info(f"Loaded {len(wara_recs)} WARA recommendations")
            except Exception as e:
                LOGGER.warning(f"Could not load WARA data: {e}")
        
        if 'RESILIENCE_MODULE' in include_sources:
            try:
                resilience_recs = self._load_resilience_recommendations(subscription_id)
                LOGGER.info(f"Loaded {len(resilience_recs)} resilience recommendations")
            except Exception as e:
                LOGGER.warning(f"Could not load resilience data: {e}")
        
        # Deduplicate and merge
        unified = self._deduplicate_and_merge(wara_recs, resilience_recs)
        
        # Sort by impact (High > Medium > Low)
        impact_order = {"High": 0, "Medium": 1, "Low": 2}
        unified.sort(
            key=lambda r: (
                impact_order.get(r.impact, 3),
                r.resource_name
            )
        )
        
        # Build response
        source_breakdown = self._count_by_source(unified)
        
        return {
            "subscription_id": subscription_id,
            "recommendations": [asdict(r) for r in unified],
            "total_count": len(unified),
            "unique_count": len(set(r.id for r in unified)),
            "by_source": source_breakdown,
            "deduplication_info": {
                "wara_provided": len(wara_recs),
                "resilience_provided": len(resilience_recs),
                "deduplicated": (len(wara_recs) + len(resilience_recs)) - len(unified),
                "consolidation_rate": f"{((len(wara_recs) + len(resilience_recs)) - len(unified)) / (len(wara_recs) + len(resilience_recs)) * 100:.1f}%" if (wara_recs or resilience_recs) else "0%"
            }
        }
    
    def _load_wara_recommendations(self, subscription_id: str) -> List[Dict[str, Any]]:
        """Load recommendations from WARA file."""
        wara_path = self._find_wara_file(subscription_id)
        if not wara_path:
            return []
        
        with open(wara_path, 'r') as f:
            wara_data = json.load(f)
        
        # Convert WARA advisory to unified format
        recommendations = []
        for adv in wara_data.get('advisory', []):
            recommendations.append({
                'recommendationId': adv.get('recommendationId'),
                'description': adv.get('description'),
                'category': adv.get('category'),
                'impact': adv.get('impact'),
                'type': adv.get('type'),
                'id': adv.get('id'),
                'name': adv.get('name'),
                'resource_group': adv.get('resourceGroup'),
                'location': adv.get('location'),
                'source': 'WARA',
                'raw_data': adv
            })
        
        return recommendations
    
    def _load_resilience_recommendations(self, subscription_id: str) -> List[Dict[str, Any]]:
        """Load recommendations from resilience module."""
        resilience_path = self.data_dir / subscription_id / "resilience_evaluations.json"
        
        if not resilience_path.exists():
            return []
        
        with open(resilience_path, 'r') as f:
            resilience_data = json.load(f)
        
        # Convert resilience findings to unified format
        recommendations = []
        for resource_id, evaluation in resilience_data.get('evaluations', {}).items():
            for finding in evaluation.get('findings', []):
                recommendations.append({
                    'recommendation_id': finding.get('recommendation_id'),
                    'description': finding.get('description'),
                    'category': finding.get('category'),
                    'impact': finding.get('impact'),
                    'resource_type': evaluation.get('resource_type'),
                    'resource_id': resource_id,
                    'resource_name': evaluation.get('resource_name'),
                    'source': 'RESILIENCE_MODULE',
                    'raw_data': {
                        'finding': finding,
                        'evaluation': {
                            'resource_type': evaluation.get('resource_type'),
                            'resource_name': evaluation.get('resource_name'),

                        }
                    }
                })
        
        return recommendations
    
    def _deduplicate_and_merge(
        self,
        wara_recs: List[Dict[str, Any]],
        resilience_recs: List[Dict[str, Any]]
    ) -> List[UnifiedRecommendation]:
        """
        Deduplicate recommendations from both sources.
        
        Strategy:
        1. Use ID matching first (WARA ID → Resiliency ID mapping)
        2. Use description similarity matching
        3. Keep unmatched recommendations from both sources
        """
        unified = []
        matched_resilience_indices = set()
        
        # Process each WARA recommendation
        for wara_rec in wara_recs:
            wara_id = wara_rec.get('recommendationId')
            matched_resilience = None
            matched_confidence = 0.0
            
            # Try to find matching resilience recommendation
            for idx, res_rec in enumerate(resilience_recs):
                if idx in matched_resilience_indices:
                    continue
                
                should_match, confidence = self.matcher.should_match(wara_rec, res_rec)
                
                if should_match and confidence > matched_confidence:
                    matched_resilience = (idx, res_rec, confidence)
                    matched_confidence = confidence
            
            # Create unified recommendation
            unified_rec = UnifiedRecommendation(
                id=wara_id or wara_rec.get('id'),
                description=wara_rec.get('description'),
                category=wara_rec.get('category'),
                impact=wara_rec.get('impact', 'Medium'),
                resource_id=wara_rec.get('id'),
                resource_type=wara_rec.get('type'),
                resource_name=wara_rec.get('name'),
                sources=['WARA'],
                source_ids={'WARA': wara_id},
                wara_data=wara_rec.get('raw_data'),
                resilience_data=matched_resilience[1].get('raw_data') if matched_resilience else None,
                potential_benefits=wara_rec.get('raw_data', {}).get('potentialBenefits'),
                long_description=wara_rec.get('raw_data', {}).get('longDescription'),
                confidence_score=matched_confidence if matched_resilience else 1.0
            )
            
            # Add resilience data if matched
            if matched_resilience:
                idx, res_rec, confidence = matched_resilience
                unified_rec.sources.append('RESILIENCE_MODULE')
                unified_rec.source_ids['RESILIENCE_MODULE'] = res_rec.get('recommendation_id')
                unified_rec.resilience_data = res_rec.get('raw_data')
                matched_resilience_indices.add(idx)
                unified_rec.confidence_score = confidence
            
            unified.append(unified_rec)
        
        # Add unmatched resilience recommendations
        for idx, res_rec in enumerate(resilience_recs):
            if idx not in matched_resilience_indices:
                unified_rec = UnifiedRecommendation(
                    id=res_rec.get('recommendation_id'),
                    description=res_rec.get('description'),
                    category=res_rec.get('category'),
                    impact=res_rec.get('impact', 'Medium'),
                    resource_id=res_rec.get('resource_id'),
                    resource_type=res_rec.get('resource_type'),
                    resource_name=res_rec.get('resource_name'),
                    sources=['RESILIENCE_MODULE'],
                    source_ids={'RESILIENCE_MODULE': res_rec.get('recommendation_id')},
                    resilience_data=res_rec.get('raw_data'),
                    confidence_score=1.0
                )
                unified.append(unified_rec)
        
        return unified
    
    def _count_by_source(self, unified: List[UnifiedRecommendation]) -> Dict[str, Any]:
        """Count recommendations by source."""
        by_source = {
            'WARA_only': 0,
            'RESILIENCE_only': 0,
            'BOTH': 0
        }
        
        for rec in unified:
            if len(rec.sources) == 2:
                by_source['BOTH'] += 1
            elif 'WARA' in rec.sources:
                by_source['WARA_only'] += 1
            else:
                by_source['RESILIENCE_only'] += 1
        
        return by_source
    
    def _find_wara_file(self, subscription_id: str) -> Optional[Path]:
        """Find the latest WARA file for a subscription."""
        pattern = self.data_dir / subscription_id / "WARA-*.json"
        files = list(self.data_dir.glob(f"{subscription_id}/WARA-*.json"))
        
        if not files:
            return None
        
        # Return most recent file
        return sorted(files)[-1]
    
    def get_recommendations_by_resource(
        self,
        subscription_id: str,
        resource_id: str
    ) -> Dict[str, Any]:
        """Get unified recommendations for a specific resource."""
        all_recs = self.get_unified_recommendations(subscription_id)
        
        resource_id_lower = resource_id.lower()
        matching = [
            r for r in all_recs['recommendations']
            if r['resource_id'].lower() == resource_id_lower
        ]
        
        return {
            "resource_id": resource_id,
            "recommendations": matching,
            "total_count": len(matching),
            "by_source": self._count_by_source([
                UnifiedRecommendation(**r) for r in matching
            ])
        }
    
    def get_recommendations_by_category(
        self,
        subscription_id: str,
        category: str
    ) -> Dict[str, Any]:
        """Get unified recommendations filtered by category."""
        all_recs = self.get_unified_recommendations(subscription_id)
        
        matching = [
            r for r in all_recs['recommendations']
            if r['category'].lower() == category.lower()
        ]
        
        return {
            "category": category,
            "recommendations": matching,
            "total_count": len(matching)
        }
