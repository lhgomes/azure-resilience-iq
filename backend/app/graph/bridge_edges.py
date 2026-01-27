"""Utility to create bridge edges when nodes are hidden or non-monitored."""

import logging
from app.intent.manual_edge import ManualEdge
from app.storage.manual_edges_store import load_manual_edges, replace_edges_for_origin
from app.graph.builder import edge_id

LOGGER = logging.getLogger(__name__)


def create_bridge_edges_for_hidden_node(subscription_id: str, hidden_node_id: str, edges: list, nodes: list) -> list:
    """
    When a node is hidden, create bridge edges to maintain dependency chains.
    
    For a hidden node:
    - Find all incoming edges (source → hidden_node)
    - Find all outgoing edges (hidden_node → target)
    - Walk forward/backward through hidden nodes to find visible endpoints
    - Create bridge edges connecting visible source to visible target
    
    Args:
        subscription_id: Subscription ID
        hidden_node_id: Node ID being hidden
        edges: All edges from the graph (can be Edge objects or dicts)
        nodes: All nodes from the graph (can be Node objects or dicts)
    
    Returns:
        List of created bridge edges (as dicts) or empty list if none created
    """
    LOGGER.info(f"[Bridge] Creating bridge edges for hidden node: {hidden_node_id}")
    LOGGER.info(f"[Bridge] Graph has {len(nodes)} nodes and {len(edges)} edges")
    
    # Convert nodes to dict for easier access
    node_dict = {}
    node_ids_set = set()
    for n in nodes:
        node_id = n.get("id") if isinstance(n, dict) else getattr(n, "id", None)
        node_ids_set.add(node_id)
        node_dict[node_id] = n
    
    # Get hidden nodes (check metadata.hidden flag)
    hidden_node_ids = set()
    for n in nodes:
        if isinstance(n, dict):
            if n.get("metadata", {}).get("hidden"):
                hidden_node_ids.add(n.get("id"))
        else:
            meta = getattr(n, "metadata", {}) or {}
            if isinstance(meta, dict) and meta.get("hidden"):
                hidden_node_ids.add(getattr(n, "id"))
    
    # Important: include the node we're currently hiding (not yet reflected in snapshot)
    hidden_node_ids.add(hidden_node_id)
    
    visible_node_ids = node_ids_set - hidden_node_ids
    
    LOGGER.info(f"[Bridge] Node IDs: {len(node_ids_set)} total, {len(hidden_node_ids)} hidden (before), {len(visible_node_ids)} visible")
    LOGGER.info(f"[Bridge] Hidden node being processed: {hidden_node_id}")
    
    # Build adjacency map for incoming edges - convert edges to dicts for uniform access
    incoming = {}  # hidden_node_id -> [(source, relationship, confidence), ...]
    
    for edge in edges:
        # Handle both Edge objects and dicts
        if isinstance(edge, dict):
            status = edge.get("status")
            src = edge.get("source")
            tgt = edge.get("target")
            rel = edge.get("relationship", "related_to")
            conf = edge.get("confidence", 0.5)
        else:
            status = getattr(edge, "status", None)
            src = getattr(edge, "source", None)
            tgt = getattr(edge, "target", None)
            rel = getattr(edge, "relationship", "related_to")
            conf = getattr(edge, "confidence", 0.5)
        
        if status == "rejected":
            continue
            
        if tgt == hidden_node_id and src in node_ids_set:
            if hidden_node_id not in incoming:
                incoming[hidden_node_id] = []
            incoming[hidden_node_id].append((src, rel, conf))
    
    LOGGER.info(f"[Bridge] Found {len(incoming.get(hidden_node_id, []))} incoming edges")
    
    # For each incoming edge, find all downstream visible nodes (once, reused for all sources)
    bridge_edges_to_save: list[ManualEdge] = []
    
    if hidden_node_id in incoming:
        # Find visible descendants once instead of per incoming edge
        visible_targets = _find_visible_descendants(hidden_node_id, edges, visible_node_ids, hidden_node_ids)
        LOGGER.info(f"[Bridge] Found {len(visible_targets)} visible descendants from {hidden_node_id}")
        
        # Create bridges from each visible source to each visible target
        for src, rel, conf in incoming[hidden_node_id]:
            if src not in visible_node_ids:
                LOGGER.info(f"[Bridge] Skipping incoming edge from {src} (not visible)")
                continue
            
            for target in visible_targets:
                if target != src:
                    eid = edge_id(src, target, rel)
                    bridge_edges_to_save.append(
                        ManualEdge(
                            id=eid,
                            source=src,
                            target=target,
                            relationship=rel,
                            confidence=conf,
                            status="accepted",
                            origin="bridge",
                            created_by="system",
                        )
                    )
                    LOGGER.info(f"[Bridge] Created bridge edge: {src} -> {target} ({rel})")
    
    # Deduplicate bridge edges by (source, target) pair - keep only one bridge per node pair
    # This prevents multiple bridges for the same path but with different relationship types
    dedup_by_pair = {}
    for e in bridge_edges_to_save:
        pair = (e.source, e.target)
        if pair not in dedup_by_pair:
            dedup_by_pair[pair] = e
    
    dedup = dedup_by_pair
    
    LOGGER.info(f"[Bridge] Total bridge edges to save: {len(dedup)}")
    
    # Replace all bridge edges for this subscription
    if dedup:
        replace_edges_for_origin(subscription_id, "bridge", list(dedup.values()))
        LOGGER.info(f"[Bridge] Saved {len(dedup)} bridge edges")
    else:
        LOGGER.info(f"[Bridge] No bridge edges to save")
    
    # Return the created edges as dicts for the API response
    return [
        {
            "id": e.id,
            "source": e.source,
            "target": e.target,
            "relationship": e.relationship,
            "confidence": e.confidence,
            "status": e.status,
            "origin": e.origin,
            "created_by": e.created_by,
        }
        for e in dedup.values()
    ]


def _find_visible_descendants(start_node: str, edges: list, visible_ids: set, hidden_ids: set) -> set:
    """Find all visible nodes reachable from start_node through hidden nodes."""
    visible_descendants = set()
    visited = {start_node}
    stack = [start_node]
    
    while stack:
        current = stack.pop()
        
        for edge in edges:
            # Handle both Edge objects and dicts
            if isinstance(edge, dict):
                src = edge.get("source")
                tgt = edge.get("target")
            else:
                src = getattr(edge, "source", None)
                tgt = getattr(edge, "target", None)
            
            if src != current:
                continue
            
            next_node = tgt
            if next_node in visited:
                continue
            visited.add(next_node)
            
            if next_node in visible_ids:
                visible_descendants.add(next_node)
            elif next_node in hidden_ids:
                stack.append(next_node)
    
    return visible_descendants


def create_bridge_edges_for_non_monitored(
    edges: list, 
    visible_node_ids: set
) -> list:
    """
    Create bridge edges that connect monitored resources through non-monitored intermediates.
    
    When non-monitored resources exist in the topology (e.g., subnets between VMs and VNets),
    create direct edges between the monitored endpoints to preserve the relationship.
    
    Args:
        edges: All edges from the graph (can be Edge objects or dicts)
        visible_node_ids: Set of monitored (visible) node IDs
    
    Returns:
        List of created bridge edges (as dicts)
    """
    # Build adjacency for breadth-first search
    forward_edges = {}  # node_id -> [(target, relationship, confidence), ...]
    
    for edge in edges:
        if isinstance(edge, dict):
            src = edge.get("source")
            tgt = edge.get("target")
            rel = edge.get("relationship", "relates_to")
            conf = edge.get("confidence", 0.7)
        else:
            src = getattr(edge, "source", None)
            tgt = getattr(edge, "target", None)
            rel = getattr(edge, "relationship", "relates_to")
            conf = getattr(edge, "confidence", 0.7)
        
        if not src or not tgt:
            continue
        
        if src not in forward_edges:
            forward_edges[src] = []
        forward_edges[src].append((tgt, rel, conf))
    
    bridge_edges_by_pair = {}
    
    # For each visible source node, find visible descendants through non-visible intermediates
    for source in visible_node_ids:
        if source not in forward_edges:
            continue
        
        # BFS through non-visible nodes to find visible targets
        visited = {source}
        queue = [(source, None, None)]  # (current_node, first_rel, first_conf)
        
        while queue:
            current, first_rel, first_conf = queue.pop(0)
            
            if current not in forward_edges:
                continue
            
            for next_node, rel, conf in forward_edges[current]:
                if next_node in visited:
                    continue
                visited.add(next_node)
                
                # Use first relationship encountered in path
                path_rel = first_rel or rel
                path_conf = first_conf if first_conf is not None else conf
                
                if next_node in visible_node_ids and next_node != source:
                    # Found a visible target; create bridge edge
                    pair = (source, next_node)
                    if pair not in bridge_edges_by_pair:
                        bridge_edges_by_pair[pair] = (path_rel, path_conf)
                        LOGGER.debug(f"Bridge: {source} -> {next_node} ({path_rel}) via non-monitored intermediates")
                elif next_node not in visible_node_ids:
                    # Continue searching through non-visible node
                    queue.append((next_node, path_rel, path_conf))
    
    # Convert bridge edges to ManualEdge objects for storage
    bridge_edges = []
    for (source, target), (rel, conf) in bridge_edges_by_pair.items():
        eid = edge_id(source, target, rel)
        bridge_edges.append(
            ManualEdge(
                id=eid,
                source=source,
                target=target,
                relationship=rel,
                confidence=conf,
                status="accepted",
                origin="bridge_non_monitored",
                created_by="system",
            )
        )
    
    return [
        {
            "id": e.id,
            "source": e.source,
            "target": e.target,
            "relationship": e.relationship,
            "confidence": e.confidence,
            "status": e.status,
            "origin": e.origin,
            "created_by": e.created_by,
        }
        for e in bridge_edges
    ]
