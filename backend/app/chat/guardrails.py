"""
Semantic guardrails for chat queries using embeddings-based scope validation.

Uses Azure OpenAI embeddings to determine if a query is related to the workload,
replacing brittle regex-based approaches with semantic understanding.
"""

import logging
import numpy as np
from typing import Tuple, List, Optional
from openai import AzureOpenAI

LOGGER = logging.getLogger(__name__)


class SemanticGuardrails:
    """
    Semantic scope validation using embeddings.
    
    Validates that user queries are related to infrastructure/workload analysis
    by comparing semantic similarity to reference workload questions.
    
    Advantages over regex:
    - Semantic understanding (handles synonyms, rephrasing)
    - Not easily gamed by prompt injection
    - Automatic handling of context
    - Tunable confidence threshold
    """
    
    def __init__(self, client: AzureOpenAI, embedding_deployment: str, threshold: float = 0.50):
        """
        Initialize semantic guardrails.
        
        Args:
            client: Azure OpenAI client for embeddings
            embedding_deployment: Name of embedding model deployment
            threshold: Similarity threshold (0-1). Lower = stricter.
                      0.45 = very permissive
                      0.50 = balanced (recommended)
                      0.55 = stricter
        """
        self.client = client
        self.embedding_deployment = embedding_deployment
        self.threshold = threshold
        
        # Reference workload-related questions for semantic comparison
        self.reference_queries = [
            "Why is this resource failing resilience checks?",
            "How do I fix this storage account for high availability?",
            "How do I fix my VM?",
            "Generate Terraform code for zone redundancy",
            "What are the dependencies for this VM?",
            "Create a remediation plan for my infrastructure",
            "Which resources need updating for compliance?",
            "How can I improve availability and scalability?",
            "What does this edge represent in my infrastructure?",
            "Why is my database not zone redundant?",
            "How do I configure this for disaster recovery?",
            "Help me troubleshoot this failing resource",
            "What remediation steps should I take?",
            "Show me the architecture of this infrastructure",
            "How do I make this highly available?",
            "Fix the resilience issues in my workload",
            "What would be the cost of enabling zone redundancy?",
            "Will switching to ZRS impact performance?",
            "How much does zone redundant storage cost?",
            "What are the performance implications of this change?",
            "Is there a cost difference between LRS and ZRS?",
            "Will this configuration affect my application performance?",
        ]
        
        # Pre-compute embeddings for reference queries (cached)
        self.reference_embeddings: Optional[np.ndarray] = None
        self._embedding_cache = {}
        
        # Lazy load reference embeddings on first use
        self._reference_embeddings_loaded = False
    
    async def _load_reference_embeddings(self) -> None:
        """Load and cache reference query embeddings."""
        if self._reference_embeddings_loaded:
            return
        
        LOGGER.info(
            f"Loading reference embeddings for {len(self.reference_queries)} queries...  "
            f"(one-time setup)"
        )
        
        try:
            embeddings = []
            for query in self.reference_queries:
                embedding = await self._embed_query(query)
                embeddings.append(embedding)
            
            self.reference_embeddings = np.array(embeddings)
            self._reference_embeddings_loaded = True
            LOGGER.debug(
                f"✓ Loaded {len(self.reference_queries)} reference embeddings "
                f"(shape: {self.reference_embeddings.shape})"
            )
        except Exception as e:
            LOGGER.error(f"Failed to load reference embeddings: {e}")
            raise
    
    async def _embed_query(self, text: str) -> List[float]:
        """
        Get embedding for a single query.
        
        Uses cache to avoid redundant API calls for the same query.
        """
        # Check cache first
        if text in self._embedding_cache:
            return self._embedding_cache[text]
        
        try:
            response = self.client.embeddings.create(
                model=self.embedding_deployment,
                input=text
            )
            embedding = response.data[0].embedding
            
            # Cache for future use
            self._embedding_cache[text] = embedding
            
            return embedding
        except Exception as e:
            LOGGER.error(f"Failed to embed query: {e}")
            raise
    
    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """Compute cosine similarity between two vectors."""
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return float(np.dot(vec1, vec2) / (norm1 * norm2))
    
    async def is_workload_related(self, query: str) -> Tuple[bool, float]:
        """
        Check if query is workload-related using semantic similarity.
        
        Args:
            query: User's input query
        
        Returns:
            (is_valid, max_similarity_score)
            
        Example:
            is_valid, similarity = await guardrails.is_workload_related("Fix my VM")
            if not is_valid:
                reject_query(f"Low relevance score: {similarity:.2f}")
        """
        # Ensure reference embeddings are loaded
        if not self._reference_embeddings_loaded:
            await self._load_reference_embeddings()
        
        try:
            # Embed the user query
            query_embedding = np.array(await self._embed_query(query))
            
            # Compute similarity to each reference embedding
            similarities = []
            for ref_embedding in self.reference_embeddings:
                sim = self._cosine_similarity(query_embedding, ref_embedding)
                similarities.append(sim)
            
            max_similarity = max(similarities) if similarities else 0.0
            is_valid = max_similarity >= self.threshold
            
            LOGGER.debug(
                f"Query scope validation: '{query[:50]}...' "
                f"similarity={max_similarity:.3f}, threshold={self.threshold}, valid={is_valid}"
            )
            
            return is_valid, max_similarity
        
        except Exception as e:
            LOGGER.error(f"Error validating query scope: {e}", exc_info=True)
            # Fail open: allow query if validation fails (service degradation)
            # Log the failure for monitoring
            return True, 0.0
    
    async def validate_query_full(self, query: str) -> Tuple[bool, str]:
        """
        Full query validation with reasoning.
        
        Returns:
            (is_valid, reason_if_invalid)
        """
        is_valid, similarity = await self.is_workload_related(query)
        
        if not is_valid:
            reason = (
                f"Your question doesn't seem related to your infrastructure "
                f"(relevance score: {similarity:.2f}). "
                f"Please ask about resources, issues, fixes, or remediation."
            )
            return False, reason
        
        return True, ""
