from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, TypeVar

from app.config import DATA_DIR
from app.storage.data_repository import get_data_repository

T = TypeVar("T")


def read_json(path: Path, default: T) -> T:
    return get_data_repository().read_json(path, default)


def write_json(path: Path, payload: Any, *, indent: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: write to a temp file in the same directory then replace.
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=indent, ensure_ascii=False)
        tmp_path.replace(path)
        get_data_repository().write_text(path, path.read_text(encoding="utf-8"), encoding="utf-8")
    finally:
        try:
            if tmp_path.exists() and tmp_path != path:
                tmp_path.unlink(missing_ok=True)
        except Exception:
            # Best-effort cleanup; don't mask the original error.
            pass


def path_exists(path: Path) -> bool:
    return get_data_repository().exists(path)


def delete_path(path: Path) -> None:
    get_data_repository().delete_file(path)


def list_data_dirs(path: Path) -> list[Path]:
    return get_data_repository().list_dirs(path)


def list_data_files(path: Path, pattern: str = "*") -> list[Path]:
    return get_data_repository().list_files(path, pattern)
