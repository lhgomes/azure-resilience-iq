/**
 * Determines the closest handle side for a source node to connect to a target node
 * Returns one of: 'top', 'bottom', 'left', 'right'
 */
export function getClosestHandle(
  sourcePos: { x: number; y: number },
  targetPos: { x: number; y: number }
): string {
  const dx = targetPos.x - sourcePos.x;
  const dy = targetPos.y - sourcePos.y;

  // Compare absolute distances
  const absX = Math.abs(dx);
  const absY = Math.abs(dy);

  // Prefer vertical routing unless horizontal distance is significantly larger
  if (absX > absY * 1.5) {
    // Horizontal is dominant
    return dx > 0 ? "right" : "left";
  } else {
    // Vertical is dominant or diagonal
    return dy > 0 ? "bottom" : "top";
  }
}

/**
 * Calculates both source and target handles for an edge
 */
export function getEdgeHandles(
  sourcePos: { x: number; y: number },
  targetPos: { x: number; y: number }
): { sourceHandle: string; targetHandle: string } {
  const sourceHandle = getClosestHandle(sourcePos, targetPos);

  // Map directions to actual handle IDs on AzureNode
  const sourceMap: Record<string, string> = {
    top: "top-source",
    bottom: "bottom",
    left: "left-source",
    right: "right",
  };

  const targetMap: Record<string, string> = {
    top: "top",
    bottom: "bottom-target",
    left: "left",
    right: "right-target",
  };

  const mappedSource = sourceMap[sourceHandle] ?? "bottom";

  const opposites: Record<string, string> = {
    top: "bottom",
    bottom: "top",
    left: "right",
    right: "left",
  };
  const targetDir = opposites[sourceHandle];
  const mappedTarget = targetMap[targetDir] ?? "top";

  return { sourceHandle: mappedSource, targetHandle: mappedTarget };
}
