from __future__ import annotations

import hashlib
import re


def service_group_name(group_id: str) -> str:
    """
    Deterministic Service Group resource name derived from the internal group id.

    Must satisfy the ARM constraint ``^[a-zA-Z0-9\\-_().]{1,90}$``. The internal
    group id is a UUID, so the ``sg-<uuid>`` form is always valid, globally
    stable, and idempotent across re-applies.
    """
    base = f"sg-{group_id}"
    cleaned = re.sub(r"[^a-zA-Z0-9\-_().]", "-", base)
    return cleaned[:90]


def member_name(resource_id: str) -> str:
    """
    Deterministic Service Group member (relationship) resource name.

    Must satisfy ``^[a-zA-Z0-9]{3,64}$``. Derived from a hash of the canonical
    resource id so the same member always maps to the same relationship name,
    keeping re-applies idempotent.
    """
    digest = hashlib.sha256((resource_id or "").encode("utf-8")).hexdigest()
    return f"m{digest[:32]}"
