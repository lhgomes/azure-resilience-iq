from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, TypeVar

from app.config import DATA_DIR

T = TypeVar("T")


def read_json(path: Path, default: T) -> T:
    if not path.exists():
        return default

    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write_json(path: Path, payload: Any, *, indent: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: write to a temp file in the same directory then replace.
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=indent, ensure_ascii=False)
        tmp_path.replace(path)
    finally:
        try:
            if tmp_path.exists() and tmp_path != path:
                tmp_path.unlink(missing_ok=True)
        except Exception:
            # Best-effort cleanup; don't mask the original error.
            pass
