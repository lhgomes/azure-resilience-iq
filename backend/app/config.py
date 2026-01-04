from __future__ import annotations

import os
from pathlib import Path

# Centralized paths for all filesystem-backed artifacts.
# Defaults preserve the current repo layout.

DATA_DIR = Path(os.getenv("AZURE_WORKLOAD_GRAPH_DATA_DIR", "data"))

COLLECTOR_DIR = DATA_DIR / "collector"
COLLECTOR_RESOURCES_PATH = COLLECTOR_DIR / "resources.json"
