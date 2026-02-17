from typing import Optional


def build_scoped_memory_key(
    *,
    subscription_id: Optional[str],
    workload_id: Optional[str] = None,
    module: Optional[str] = None,
    resource_type: Optional[str] = None,
) -> str:
    """Build a deterministic scoped memory key for Foundry thread isolation."""
    safe_subscription = (subscription_id or "unknown").strip() or "unknown"

    parts = [f"sub:{safe_subscription}"]

    if module and str(module).strip():
        parts.append(f"mod:{str(module).strip()}")

    if workload_id and str(workload_id).strip():
        parts.append(f"wl:{str(workload_id).strip()}")

    if resource_type and str(resource_type).strip():
        normalized_type = str(resource_type).strip().replace(" ", "_")
        parts.append(f"rtype:{normalized_type}")

    return "|".join(parts)
