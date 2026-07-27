"""Ingest documents into Azure AI Search for RAG.

Usage examples:
    # APRL + unified Terraform modules ingestion on the same endpoint/key
    python -m app.llm.ingest_search \
        --targets aprl,terraform \
        --aprl-local-path ./aprl \
        --terraform-local-path ./docs/terraform \
        --aprl-index-name learn-aprl-index \
        --terraform-index-name learn-terraform-index

Required configuration (env or args):
- AZURE_SEARCH_ENDPOINT
- --embedding-model (or AI_FOUNDRY_EMBEDDING_MODEL / ai_agent.embedding_model)
- Foundry project configuration (AI_FOUNDRY_PROJECT_ENDPOINT)
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient

from app.logger import get_logger, setup_logging
from app.llm.model_client import create_model_client
from app.settings import get_settings, load_settings

LOGGER = get_logger(__name__)


@dataclass
class SourceDocument:
    title: str
    content: str
    url: str
    source: str
    module: str
    service: str
    aprl_id: str
    last_updated: str


@dataclass
class IngestionTarget:
    corpus: str
    index_name: Optional[str]
    local_paths: List[str]
    url_file: Optional[str]


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _strip_markdown(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!?\[([^\]]*)\]\(([^\)]*)\)", r"\1", text)
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    return _normalize_whitespace(text)


def _chunk_text(text: str, chunk_chars: int, overlap_chars: int) -> List[str]:
    if not text:
        return []
    if len(text) <= chunk_chars:
        return [text]

    chunks: List[str] = []
    start = 0
    size = max(400, chunk_chars)
    overlap = max(0, min(overlap_chars, size // 2))

    while start < len(text):
        end = min(start + size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap

    return chunks


def _service_from_path(path: Path) -> str:
    parts = [part.lower() for part in path.parts]
    if "azure-resources" in parts:
        idx = parts.index("azure-resources")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    if "aprl" in parts:
        idx = parts.index("aprl")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return "general"


def _aprl_id_from_path(path: Path) -> str:
    parts = list(path.parts)
    parts_lower = [part.lower() for part in parts]

    if "azure-resources" in parts_lower:
        idx = parts_lower.index("azure-resources")
        return "/".join(parts[idx:])

    if "aprl" in parts_lower:
        idx = parts_lower.index("aprl")
        return "/".join(parts[idx:])

    return path.stem


def _collect_local_documents(paths: List[str], include_glob: str) -> List[SourceDocument]:
    documents: List[SourceDocument] = []
    now = datetime.now(timezone.utc).isoformat()
    allowed_suffixes = {".md", ".txt", ".rst", ".yaml", ".yml", ".kql"}

    for path_text in paths:
        root = Path(path_text)
        if not root.exists():
            LOGGER.warning("Local path not found, skipping: %s", root)
            continue

        files = [root] if root.is_file() else list(root.glob(include_glob))
        for file_path in files:
            if not file_path.is_file():
                continue
            suffix = file_path.suffix.lower()
            if suffix not in allowed_suffixes:
                continue

            try:
                raw = file_path.read_text(encoding="utf-8", errors="ignore")
                content = _strip_markdown(raw) if suffix in {".md", ".txt", ".rst"} else _normalize_whitespace(raw)
                if not content:
                    continue
                is_aprl_path = "aprl" in str(file_path).lower()
                documents.append(
                    SourceDocument(
                        title=file_path.stem,
                        content=content,
                        url=str(file_path),
                        source="APRL" if is_aprl_path else "LocalDocs",
                        module="aprl" if is_aprl_path else "localdocs",
                        service=_service_from_path(file_path),
                        aprl_id=_aprl_id_from_path(file_path) if is_aprl_path else "",
                        last_updated=now,
                    )
                )
            except Exception as error:
                LOGGER.warning("Failed to read %s: %s", file_path, error)

    return documents


def _collect_local_documents_for_corpus(
    corpus: str,
    paths: List[str],
    include_glob: str,
) -> List[SourceDocument]:
    documents = _collect_local_documents(paths, include_glob)
    corpus_key = (corpus or "").strip().lower()
    source_label = {
        "aprl": "APRL",
        "terraform": "TerraformModules",
    }.get(corpus_key, "LocalDocs")

    def _infer_module_from_text(value: str, default_module: str) -> str:
        text = value.lower()
        if (
            "azure-verified-modules" in text
            or "/avm/" in text
            or "aka.ms/avm" in text
        ):
            return "avm"
        if (
            "cloud-adoption-framework" in text
            or "terraform-azurerm-caf-enterprise-scale" in text
            or "/caf/" in text
        ):
            return "caf"
        return default_module

    for doc in documents:
        inferred_module = _infer_module_from_text(doc.url, corpus_key or "localdocs")
        if corpus_key == "terraform":
            doc.source = "AVM" if inferred_module == "avm" else "CAF" if inferred_module == "caf" else source_label
        else:
            doc.source = source_label
        doc.module = inferred_module
        if corpus_key != "aprl":
            doc.aprl_id = ""

    return documents


def _collect_url_documents(url_file: Optional[str], timeout_seconds: int) -> List[SourceDocument]:
    if not url_file:
        return []

    file_path = Path(url_file)
    if not file_path.exists():
        LOGGER.warning("URL file not found: %s", url_file)
        return []

    urls = [line.strip() for line in file_path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.strip().startswith("#")]
    if not urls:
        return []

    documents: List[SourceDocument] = []
    now = datetime.now(timezone.utc).isoformat()

    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        for url in urls:
            try:
                response = client.get(url)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "text" not in content_type and "html" not in content_type and "markdown" not in content_type:
                    LOGGER.warning("Skipping non-text URL %s (content-type=%s)", url, content_type)
                    continue

                text = response.text
                text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.IGNORECASE)
                text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
                text = re.sub(r"<[^>]+>", " ", text)
                text = _normalize_whitespace(text)
                if not text:
                    continue

                source = "MicrosoftLearn" if "learn.microsoft.com" in url.lower() else "Web"
                service = "azure" if source == "MicrosoftLearn" else "general"
                title = url.rstrip("/").split("/")[-1] or "document"

                documents.append(
                    SourceDocument(
                        title=title,
                        content=text,
                        url=url,
                        source=source,
                        module="web",
                        service=service,
                        aprl_id="",
                        last_updated=now,
                    )
                )
            except Exception as error:
                LOGGER.warning("Failed to fetch %s: %s", url, error)

    return documents


def _collect_url_documents_for_corpus(
    corpus: str,
    url_file: Optional[str],
    timeout_seconds: int,
) -> List[SourceDocument]:
    documents = _collect_url_documents(url_file, timeout_seconds)
    corpus_key = (corpus or "").strip().lower()
    source_label = {
        "aprl": "APRL",
        "terraform": "TerraformModules",
    }.get(corpus_key)

    def _infer_module_from_text(value: str, default_module: str) -> str:
        text = value.lower()
        if (
            "azure-verified-modules" in text
            or "/avm/" in text
            or "aka.ms/avm" in text
        ):
            return "avm"
        if (
            "cloud-adoption-framework" in text
            or "terraform-azurerm-caf-enterprise-scale" in text
            or "/caf/" in text
        ):
            return "caf"
        return default_module

    if source_label:
        for doc in documents:
            inferred_module = _infer_module_from_text(doc.url, corpus_key or "web")
            if corpus_key == "terraform":
                if doc.source not in {"MicrosoftLearn", "Web"}:
                    doc.source = "AVM" if inferred_module == "avm" else "CAF" if inferred_module == "caf" else source_label
            else:
                if doc.source not in {"MicrosoftLearn", "Web"}:
                    doc.source = source_label
            doc.module = inferred_module
            if corpus_key != "aprl":
                doc.aprl_id = ""

    return documents


def _split_csv(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _resolve_targets(args, ai_agent_cfg: Dict[str, Any]) -> List[IngestionTarget]:
    selected_targets = _split_csv(args.targets) or ["terraform"]
    normalized_targets: List[str] = []
    seen: set[str] = set()
    for target in selected_targets:
        normalized = target.strip().lower()
        if normalized not in {"aprl", "terraform"}:
            LOGGER.warning("Ignoring unknown target '%s' (allowed: aprl,terraform)", target)
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        normalized_targets.append(normalized)

    if not normalized_targets:
        normalized_targets = ["terraform"]

    aprl_index = (
        args.aprl_index_name
        or os.getenv("AZURE_SEARCH_INDEX_NAME_APRL")
        or ai_agent_cfg.get("index_name_aprl")
    )
    terraform_index = (
        args.terraform_index_name
        or os.getenv("AZURE_SEARCH_INDEX_NAME_TERRAFORM")
        or ai_agent_cfg.get("index_name_terraform")
    )

    aprl_paths = args.aprl_local_path or []
    terraform_paths = args.terraform_local_path or []

    aprl_url_file = args.aprl_url_file
    terraform_url_file = args.terraform_url_file

    target_map: Dict[str, IngestionTarget] = {
        "aprl": IngestionTarget(
            corpus="aprl",
            index_name=aprl_index,
            local_paths=aprl_paths,
            url_file=aprl_url_file,
        ),
        "terraform": IngestionTarget(
            corpus="terraform",
            index_name=terraform_index,
            local_paths=terraform_paths,
            url_file=terraform_url_file,
        ),
    }

    return [target_map[target_key] for target_key in normalized_targets]


def _build_chunk_records(
    corpus: str,
    documents: List[SourceDocument],
    chunk_chars: int,
    overlap_chars: int,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for doc in documents:
        chunks = _chunk_text(doc.content, chunk_chars=chunk_chars, overlap_chars=overlap_chars)
        for chunk_index, chunk in enumerate(chunks):
            identifier = hashlib.sha1(
                f"{corpus}:{doc.module}:{doc.url}:{chunk_index}:{chunk[:120]}".encode("utf-8")
            ).hexdigest()
            record = {
                "id": identifier,
                "title": doc.title,
                "content": chunk,
                "corpus": corpus,
                "source": doc.source,
                "source_type": doc.module,
                "chunk_no": chunk_index,
            }
            records.append(record)
    return records


def _embed_records(
    records: List[Dict[str, Any]],
    model_client,
    embedding_model: str,
    batch_size: int,
    max_retries: int,
    retry_base_seconds: int,
    retry_max_seconds: int,
) -> None:
    if not model_client or not model_client.is_available():
        raise RuntimeError("Foundry model client is not available for embeddings")

    def _is_rate_limit_error(error: Exception) -> bool:
        text = str(error).lower()
        return " 429" in text or "(429)" in text or "rate limit" in text

    def _extract_retry_after_seconds(error: Exception) -> int:
        text = str(error)
        patterns = [
            r"retry\s+after\s+(\d+)\s+seconds",
            r"retry-after\D*(\d+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                try:
                    return max(1, int(match.group(1)))
                except ValueError:
                    continue
        return 0

    max_batch_size = max(1, min(batch_size, 32))
    current_batch_size = max_batch_size

    start = 0
    while start < len(records):
        batch = records[start:start + current_batch_size]
        inputs = [item["content"] for item in batch]
        attempt = 0

        while True:
            try:
                vectors = model_client.embed_texts(
                    inputs=inputs,
                    embedding_model=embedding_model,
                )

                for item, vector in zip(batch, vectors):
                    item["content_vector"] = vector

                start += len(batch)

                if current_batch_size < max_batch_size:
                    current_batch_size = min(max_batch_size, current_batch_size + 1)

                break
            except Exception as error:
                if not _is_rate_limit_error(error):
                    raise

                if attempt >= max_retries:
                    raise RuntimeError(
                        "Embedding requests exceeded retry limit after repeated 429 responses"
                    ) from error

                previous_batch_size = current_batch_size
                if current_batch_size > 1:
                    current_batch_size = max(1, current_batch_size // 2)
                    if current_batch_size != previous_batch_size:
                        LOGGER.warning(
                            "Embedding throttled (429). Shrinking batch size from %d to %d",
                            previous_batch_size,
                            current_batch_size,
                        )
                        batch = records[start:start + current_batch_size]
                        inputs = [item["content"] for item in batch]

                retry_after = _extract_retry_after_seconds(error)
                exponential = retry_base_seconds * (2 ** attempt)
                wait_seconds = retry_after if retry_after > 0 else exponential
                wait_seconds = max(1, min(wait_seconds, retry_max_seconds))

                LOGGER.warning(
                    "Embedding throttled (429). Retrying in %ds at batch_size=%d (attempt %d/%d)",
                    wait_seconds,
                    current_batch_size,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(wait_seconds)
                attempt += 1


def _upload_records(
    endpoint: str,
    index_name: str,
    records: List[Dict[str, Any]],
    batch_size: int,
) -> None:
    client = SearchClient(
        endpoint=endpoint,
        index_name=index_name,
        credential=DefaultAzureCredential(),
    )

    batch_size = max(1, min(batch_size, 1000))
    for start in range(0, len(records), batch_size):
        batch = records[start:start + batch_size]
        result = client.merge_or_upload_documents(documents=batch)
        failed = [item for item in result if not item.succeeded]
        if failed:
            raise RuntimeError(f"Search upload failed for {len(failed)} documents")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest documents into Azure AI Search for RAG")
    parser.add_argument(
        "--targets",
        default="terraform",
        help="Comma-separated ingestion targets: aprl,terraform (default: terraform)",
    )
    parser.add_argument("--include-glob", default="**/*", help="Glob for files when local path is a directory")
    parser.add_argument("--aprl-local-path", action="append", default=[], help="APRL local directory/file (repeatable)")
    parser.add_argument("--terraform-local-path", action="append", default=[], help="Terraform modules (AVM+CAF) local directory/file (repeatable)")
    parser.add_argument("--aprl-url-file", default=None, help="APRL URL list file")
    parser.add_argument("--terraform-url-file", default=None, help="Terraform modules (AVM+CAF) URL list file")
    parser.add_argument("--aprl-index-name", default=None, help="APRL Azure AI Search index name")
    parser.add_argument("--terraform-index-name", default=None, help="Terraform modules (AVM+CAF) Azure AI Search index name")
    parser.add_argument("--search-endpoint", default=None, help="Azure AI Search endpoint")
    parser.add_argument("--embedding-model", default=None, help="Embedding model/deployment name")
    parser.add_argument("--chunk-chars", type=int, default=2200, help="Chunk size in characters")
    parser.add_argument("--overlap-chars", type=int, default=250, help="Chunk overlap in characters")
    parser.add_argument("--embed-batch-size", type=int, default=16, help="Embedding batch size")
    parser.add_argument("--embed-max-retries", type=int, default=6, help="Max retries per embedding batch on 429 throttling")
    parser.add_argument("--embed-retry-base-seconds", type=int, default=5, help="Base backoff seconds for embedding retries")
    parser.add_argument("--embed-retry-max-seconds", type=int, default=90, help="Max wait seconds for embedding retries")
    parser.add_argument("--upload-batch-size", type=int, default=200, help="Upload batch size")
    parser.add_argument("--http-timeout", type=int, default=30, help="HTTP timeout for URL downloads")
    parser.add_argument("--dry-run", action="store_true", help="Prepare records without embedding/upload")
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Override log level",
    )
    args = parser.parse_args()

    load_settings()
    setup_logging(args.log_level)
    settings = get_settings()

    search_endpoint = args.search_endpoint or os.getenv("AZURE_SEARCH_ENDPOINT")
    ai_agent_cfg = settings.get_ai_agent_config()
    model_client = create_model_client(settings)
    targets = _resolve_targets(args, ai_agent_cfg)

    embedding_model = (
        args.embedding_model
        or os.getenv("AI_FOUNDRY_EMBEDDING_MODEL")
        or ai_agent_cfg.get("embedding_model")
    )

    if not any(target.local_paths or target.url_file for target in targets):
        LOGGER.error(
            "At least one input source is required. Provide APRL/Terraform inputs via "
            "target-specific arguments."
        )
        return 1

    if not embedding_model:
        LOGGER.error(
            "Missing embedding model configuration. Provide --embedding-model "
            "or set AI_FOUNDRY_EMBEDDING_MODEL (or ai_agent.embedding_model)."
        )
        return 1
    if not model_client.is_available():
        LOGGER.error("Foundry model client unavailable. Configure AI_FOUNDRY_PROJECT_ENDPOINT.")
        return 1

    try:
        ingested_targets = 0
        for target in targets:
            documents = _collect_local_documents_for_corpus(
                corpus=target.corpus,
                paths=target.local_paths,
                include_glob=args.include_glob,
            )
            documents.extend(
                _collect_url_documents_for_corpus(
                    corpus=target.corpus,
                    url_file=target.url_file,
                    timeout_seconds=args.http_timeout,
                )
            )

            if not documents:
                LOGGER.info("No documents collected for target '%s'; skipping", target.corpus)
                continue

            records = _build_chunk_records(
                corpus=target.corpus,
                documents=documents,
                chunk_chars=args.chunk_chars,
                overlap_chars=args.overlap_chars,
            )
            LOGGER.info(
                "[%s] Collected %d documents, generated %d chunks",
                target.corpus.upper(),
                len(documents),
                len(records),
            )

            LOGGER.info("[%s] Generating embeddings with model '%s'", target.corpus.upper(), embedding_model)
            _embed_records(
                records=records,
                model_client=model_client,
                embedding_model=embedding_model,
                batch_size=args.embed_batch_size,
                max_retries=max(0, args.embed_max_retries),
                retry_base_seconds=max(1, args.embed_retry_base_seconds),
                retry_max_seconds=max(1, args.embed_retry_max_seconds),
            )

            if args.dry_run:
                LOGGER.info("[%s] Dry run enabled. Skipping upload.", target.corpus.upper())
                ingested_targets += 1
                continue

            if not search_endpoint:
                LOGGER.error("Missing search configuration. Provide search endpoint via args or env vars.")
                return 1
            if not target.index_name:
                LOGGER.error(
                    "[%s] Missing index name. Set target-specific index argument or env var.",
                    target.corpus.upper(),
                )
                return 1

            LOGGER.info(
                "[%s] Uploading %d chunks to index '%s'",
                target.corpus.upper(),
                len(records),
                target.index_name,
            )
            _upload_records(
                endpoint=search_endpoint,
                index_name=target.index_name,
                records=records,
                batch_size=args.upload_batch_size,
            )
            ingested_targets += 1

        if ingested_targets == 0:
            LOGGER.warning("No targets were ingested (all had empty inputs).")
            return 0
    except Exception:
        LOGGER.exception("Ingestion failed")
        return 1

    LOGGER.info("✓ Ingestion complete for %d target(s)", ingested_targets)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
