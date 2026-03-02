from __future__ import annotations

import fnmatch
import json
import logging
from pathlib import Path
from typing import Any

from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

from app.config import DATA_DIR
from app.settings import get_settings

LOGGER = logging.getLogger(__name__)


class DataRepository:
    def __init__(self) -> None:
        settings = get_settings()
        cfg = settings.get_data_storage_config()
        self.data_root = DATA_DIR
        self.backend = cfg.get("backend", "local")
        self.storage_account = cfg.get("storage_account", "")
        self.container = cfg.get("container", "")
        self.prefix = cfg.get("prefix", "")
        self._blob_client: BlobServiceClient | None = None
        self._blob_enabled = (
            self.backend == "blob"
            and bool(self.storage_account)
            and bool(self.container)
        )
        if self.backend == "blob" and not self._blob_enabled:
            LOGGER.warning("Blob backend configured but storage account/container missing; falling back to local mode")

    @property
    def blob_enabled(self) -> bool:
        return self._blob_enabled

    def _client(self) -> BlobServiceClient | None:
        if not self._blob_enabled:
            return None
        if self._blob_client is not None:
            return self._blob_client
        try:
            self._blob_client = BlobServiceClient(
                account_url=f"https://{self.storage_account}.blob.core.windows.net",
                credential=DefaultAzureCredential(),
            )
        except Exception as exc:
            LOGGER.warning("Could not initialize BlobServiceClient, using local mode: %s", exc)
            self._blob_enabled = False
            return None
        return self._blob_client

    def _is_data_path(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.data_root.resolve())
            return True
        except Exception:
            return False

    def _blob_name(self, path: Path) -> str:
        rel = path.resolve().relative_to(self.data_root.resolve()).as_posix()
        rel = "" if rel == "." else rel
        if self.prefix:
            return f"{self.prefix}/{rel}" if rel else self.prefix
        return rel

    def _download_if_missing(self, path: Path) -> None:
        if path.exists() or not self._is_data_path(path):
            return
        client = self._client()
        if client is None:
            return

        blob_name = self._blob_name(path)
        container_client = client.get_container_client(self.container)
        try:
            payload = container_client.download_blob(blob_name).readall()
        except ResourceNotFoundError:
            return
        except Exception as exc:
            LOGGER.debug("Blob download skipped for %s: %s", blob_name, exc)
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    def _upload_if_blob(self, path: Path) -> None:
        if not self._is_data_path(path):
            return
        client = self._client()
        if client is None:
            return

        blob_name = self._blob_name(path)
        container_client = client.get_container_client(self.container)
        try:
            if not container_client.exists():
                container_client.create_container()
            with path.open("rb") as handle:
                container_client.upload_blob(name=blob_name, data=handle, overwrite=True)
        except Exception as exc:
            LOGGER.warning("Blob upload failed for %s: %s", blob_name, exc)

    def exists(self, path: Path) -> bool:
        if path.exists():
            return True

        if not self._is_data_path(path):
            return False

        client = self._client()
        if client is None:
            return False

        blob_name = self._blob_name(path)
        try:
            client.get_blob_client(container=self.container, blob=blob_name).get_blob_properties()
            self._download_if_missing(path)
            return path.exists()
        except ResourceNotFoundError:
            return False
        except Exception:
            return False

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        self._download_if_missing(path)
        return path.read_text(encoding=encoding)

    def write_text(self, path: Path, text: str, *, encoding: str = "utf-8") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding=encoding)
        self._upload_if_blob(path)

    def delete_file(self, path: Path) -> None:
        if path.exists():
            path.unlink(missing_ok=True)

        if not self._is_data_path(path):
            return

        client = self._client()
        if client is None:
            return

        blob_name = self._blob_name(path)
        try:
            client.get_blob_client(container=self.container, blob=blob_name).delete_blob(delete_snapshots="include")
        except ResourceNotFoundError:
            pass
        except Exception as exc:
            LOGGER.debug("Blob delete failed for %s: %s", blob_name, exc)

    def list_dirs(self, base_dir: Path) -> list[Path]:
        base_dir.mkdir(parents=True, exist_ok=True)
        dirs = {p for p in base_dir.iterdir() if p.is_dir()}

        if not self._is_data_path(base_dir):
            return sorted(dirs)

        client = self._client()
        if client is None:
            return sorted(dirs)

        prefix = self._blob_name(base_dir)
        if prefix and not prefix.endswith("/"):
            prefix = f"{prefix}/"

        try:
            container_client = client.get_container_client(self.container)
            names = set()
            for blob in container_client.list_blobs(name_starts_with=prefix):
                suffix = blob.name[len(prefix):] if prefix else blob.name
                if "/" not in suffix:
                    continue
                first = suffix.split("/", 1)[0].strip()
                if first:
                    names.add(first)
            for name in names:
                local_dir = base_dir / name
                local_dir.mkdir(parents=True, exist_ok=True)
                dirs.add(local_dir)
        except Exception as exc:
            LOGGER.debug("Blob list dirs failed for %s: %s", base_dir, exc)

        return sorted(dirs)

    def list_files(self, base_dir: Path, pattern: str = "*") -> list[Path]:
        base_dir.mkdir(parents=True, exist_ok=True)
        files = {p for p in base_dir.glob(pattern) if p.is_file()}

        if not self._is_data_path(base_dir):
            return sorted(files)

        client = self._client()
        if client is None:
            return sorted(files)

        prefix = self._blob_name(base_dir)
        if prefix and not prefix.endswith("/"):
            prefix = f"{prefix}/"

        try:
            container_client = client.get_container_client(self.container)
            for blob in container_client.list_blobs(name_starts_with=prefix):
                suffix = blob.name[len(prefix):] if prefix else blob.name
                if not suffix or "/" in suffix:
                    continue
                if not fnmatch.fnmatch(suffix, pattern):
                    continue
                local_path = base_dir / suffix
                self._download_if_missing(local_path)
                if local_path.exists():
                    files.add(local_path)
        except Exception as exc:
            LOGGER.debug("Blob list files failed for %s: %s", base_dir, exc)

        return sorted(files)

    def read_json(self, path: Path, default: Any) -> Any:
        if not self.exists(path):
            return default
        try:
            return json.loads(self.read_text(path))
        except Exception:
            return default

    def write_json(self, path: Path, payload: Any, *, indent: int = 2) -> None:
        self.write_text(path, json.dumps(payload, indent=indent, ensure_ascii=False))


_repo: DataRepository | None = None


def get_data_repository() -> DataRepository:
    global _repo
    if _repo is None:
        _repo = DataRepository()
    return _repo
