"""Batch partitioning and per-batch LLM annotation for large graphs.

Divides large graphs into stateless, self-contained batches where each node
includes global features (connection count, overrides, user intent flags) and
compact adjacency summaries. Each batch is annotated independently, results
are merged, and criticality_weight is recomputed over the full set.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from .models import LLMAnnotations

LOGGER = logging.getLogger(__name__)


class BatchPartitioner:
    """Partition nodes into self-contained batches for independent LLM processing."""

    def __init__(
        self,
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        max_nodes_per_batch: int = 50,
    ):
        """
        Initialize partitioner.

        Args:
            nodes: Full list of nodes from summarized graph.
            edges: Full list of edges from summarized graph.
            max_nodes_per_batch: Max nodes per batch (default 50).
        """
        self.nodes = nodes
        self.edges = edges
        self.max_nodes_per_batch = max_nodes_per_batch

    def compute_global_features(self) -> Dict[str, Dict[str, Any]]:
        """
        Precompute global context features for each node.

        Returns dict mapping node short_id to features:
        - connection_count: total edges connected to this node
        - criticality_override: user-authored override if present
        - node_type: resource type
        - has_manual_edges: whether this node has user-created/accepted edges
        - max_edge_confidence: highest aggregated_confidence on any edge for this node
        - adjacent_types: summary of neighbor types (dedup'd)
        """
        features: Dict[str, Dict[str, Any]] = {}

        for node in self.nodes:
            node_id = node.get("short_id") or node.get("id")
            if not node_id:
                continue

            # Initialize features for this node
            features[node_id] = {
                "connection_count": node.get("connection_count", 0),
                "criticality_override": node.get("criticality_override"),
                "node_type": node.get("type") or "unknown",
                "has_manual_edges": False,
                "max_edge_confidence": 0.0,
                "adjacent_types": [],
            }

        # Scan edges to populate manual edge flags and max confidence
        adjacent_types_map: Dict[str, set] = {n.get("short_id") or n.get("id"): set() for n in self.nodes if n.get("short_id") or n.get("id")}

        for edge in self.edges:
            source = edge.get("source") or edge.get("source_short_id")
            target = edge.get("target") or edge.get("target_short_id")
            status = edge.get("status")
            confidence = edge.get("confidence", 0.0)

            # Check if edge is user-created or accepted
            is_manual = status in ("accepted", "manual") or edge.get("source_kind") == "manual"

            # Track adjacent types
            if source in adjacent_types_map and target in adjacent_types_map:
                source_type = next((n.get("type") for n in self.nodes if (n.get("short_id") or n.get("id")) == target), "unknown")
                target_type = next((n.get("type") for n in self.nodes if (n.get("short_id") or n.get("id")) == source), "unknown")
                adjacent_types_map[source].add(target_type)
                adjacent_types_map[target].add(source_type)

            # Update manual edge flag
            if is_manual:
                if source in features:
                    features[source]["has_manual_edges"] = True
                if target in features:
                    features[target]["has_manual_edges"] = True

            # Update max confidence
            if source in features:
                features[source]["max_edge_confidence"] = max(
                    features[source]["max_edge_confidence"],
                    confidence,
                )
            if target in features:
                features[target]["max_edge_confidence"] = max(
                    features[target]["max_edge_confidence"],
                    confidence,
                )

        # Populate adjacent types
        for node_id, types in adjacent_types_map.items():
            if node_id in features:
                features[node_id]["adjacent_types"] = sorted(list(types))

        return features

    def partition(self) -> List[Dict[str, Any]]:
        """
        Partition nodes into batches, each with global features.

        Returns list of batch dicts, each containing:
        - batch_num: batch index (1-indexed)
        - nodes: list of nodes in this batch with global features
        - edges: edges between nodes in this batch
        - batch_context: string summarizing global graph properties
        """
        global_features = self.compute_global_features()
        batches = []
        batch_nodes = []

        for node in self.nodes:
            batch_nodes.append(node)
            if len(batch_nodes) >= self.max_nodes_per_batch:
                batches.append(self._create_batch(batch_nodes, global_features))
                batch_nodes = []

        # Flush remaining nodes
        if batch_nodes:
            batches.append(self._create_batch(batch_nodes, global_features))

        return batches

    def _create_batch(
        self,
        batch_nodes: List[Dict[str, Any]],
        global_features: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Create a single batch with global features and internal edges."""
        batch_node_ids = {n.get("short_id") or n.get("id") for n in batch_nodes if n.get("short_id") or n.get("id")}

        # Enrich batch nodes with global features
        enriched_nodes = []
        for node in batch_nodes:
            node_id = node.get("short_id") or node.get("id")
            if node_id:
                node = dict(node)  # shallow copy
                if node_id in global_features:
                    node.update(global_features[node_id])
                enriched_nodes.append(node)

        # Filter edges to only those within batch
        batch_edges = [
            e
            for e in self.edges
            if (e.get("source") or e.get("source_short_id")) in batch_node_ids
            and (e.get("target") or e.get("target_short_id")) in batch_node_ids
        ]

        return {
            "batch_num": len(self),  # will be set by caller
            "nodes": enriched_nodes,
            "edges": batch_edges,
            "batch_context": f"Batch with {len(enriched_nodes)} nodes and {len(batch_edges)} edges.",
        }

    def __len__(self) -> int:
        """Return number of nodes."""
        return len(self.nodes)


def merge_batch_annotations(batch_results: List[LLMAnnotations]) -> LLMAnnotations:
    """
    Merge annotations from multiple batches into a single result.

    Args:
        batch_results: List of LLMAnnotations from each batch call.

    Returns:
        Merged LLMAnnotations with deduped nodes and all edges.
    """
    merged_nodes: Dict[str, Any] = {}
    merged_edges: List[Dict[str, Any]] = []

    # Merge nodes (deduplicate by node_id)
    for batch_result in batch_results:
        for node_ann in batch_result.nodes:
            merged_nodes[node_ann.node_id] = node_ann

    # Merge edges (all edges from all batches, dedup later if needed)
    for batch_result in batch_results:
        merged_edges.extend(batch_result.edges)

    # Deduplicate edges by id
    edge_dict = {}
    for edge in merged_edges:
        edge_id = edge.id or f"{edge.source}|{edge.target}"
        edge_dict[edge_id] = edge

    from .models import NodeAnnotation

    result_nodes = [
        NodeAnnotation(node_id=nid, annotations=node.annotations)
        for nid, node in merged_nodes.items()
    ]
    result_edges = list(edge_dict.values())

    return LLMAnnotations(nodes=result_nodes, edges=result_edges)
