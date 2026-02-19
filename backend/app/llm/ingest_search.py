"""Ingest documents into Azure AI Search for RAG.

Usage example:
  python -m app.llm.ingest_search \
    --local-path ./aprl \
    --url-file ./data/learn_urls.txt \
    --index-name learn-aprl-index

Required configuration (env or args):
- AZURE_SEARCH_ENDPOINT
- AZURE_SEARCH_ADMIN_KEY
- --embedding-model (or AI_GATEWAY_EMBEDDING_MODEL / ai_agent.embedding_model)
- APIM model configuration (ai_agent.gateway_base_url + AI_GATEWAY_SUBSCRIPTION_KEY)
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from azure.core.credentials import AzureKeyCredential
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
    service: str
    aprl_id: str
    last_updated: str


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
    if "aprl" in parts:
        idx = parts.index("aprl")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return "general"


def _collect_local_documents(paths: List[str], include_glob: str) -> List[SourceDocument]:
    documents: List[SourceDocument] = []
    now = datetime.now(timezone.utc).isoformat()

    for path_text in paths:
        root = Path(path_text)
        if not root.exists():
            LOGGER.warning("Local path not found, skipping: %s", root)
            continue

        files = [root] if root.is_file() else list(root.glob(include_glob))
        for file_path in files:
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in {".md", ".txt", ".rst"}:
                continue

            try:
                raw = file_path.read_text(encoding="utf-8", errors="ignore")
                content = _strip_markdown(raw)
                if not content:
                    continue
                documents.append(
                    SourceDocument(
                        title=file_path.stem,
                        content=content,
                        url=str(file_path),
                        source="APRL" if "aprl" in str(file_path).lower() else "LocalDocs",
                        service=_service_from_path(file_path),
                        aprl_id=file_path.stem if "aprl" in str(file_path).lower() else "",
                        last_updated=now,
                    )
                )
            except Exception as error:
                LOGGER.warning("Failed to read %s: %s", file_path, error)

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
                        service=service,
                        aprl_id="",
                        last_updated=now,
                    )
                )
            except Exception as error:
                LOGGER.warning("Failed to fetch %s: %s", url, error)

    return documents


def _build_chunk_records(
    documents: List[SourceDocument],
    chunk_chars: int,
    overlap_chars: int,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for doc in documents:
        chunks = _chunk_text(doc.content, chunk_chars=chunk_chars, overlap_chars=overlap_chars)
        for chunk_index, chunk in enumerate(chunks):
            identifier = hashlib.sha1(f"{doc.url}:{chunk_index}:{chunk[:120]}".encode("utf-8")).hexdigest()
            records.append(
                {
                    "id": identifier,
                    "title": doc.title,
                    "content": chunk,
                    "url": doc.url,
                    "source": doc.source,
                    "service": doc.service,
                    "aprl_id": doc.aprl_id,
                    "last_updated": doc.last_updated,
                    "chunk_index": chunk_index,
                }
            )
    return records


def _embed_records(
    records: List[Dict[str, Any]],
    model_client,
    embedding_model: str,
    batch_size: int,
) -> None:
    if not model_client or not model_client.is_available():
        raise RuntimeError("APIM model client is not available for embeddings")

    batch_size = max(1, min(batch_size, 32))
    for start in range(0, len(records), batch_size):
        batch = records[start:start + batch_size]
        inputs = [item["content"] for item in batch]
        vectors = model_client.embed_texts(
            inputs=inputs,
            embedding_model=embedding_model,
        )
        for item, vector in zip(batch, vectors):
            item["contentVector"] = vector


def _upload_records(
    endpoint: str,
    admin_key: str,
    index_name: str,
    records: List[Dict[str, Any]],
    batch_size: int,
) -> None:
    client = SearchClient(
        endpoint=endpoint,
        index_name=index_name,
        credential=AzureKeyCredential(admin_key),
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
    parser.add_argument("--local-path", action="append", default=[], help="Local directory or file to ingest (repeatable)")
    parser.add_argument("--include-glob", default="**/*", help="Glob for files when local path is a directory")
    parser.add_argument("--url-file", help="Text file containing URLs to ingest (one URL per line)")
    parser.add_argument("--index-name", default=None, help="Azure AI Search index name")
    parser.add_argument("--search-endpoint", default=None, help="Azure AI Search endpoint")
    parser.add_argument("--search-admin-key", default=None, help="Azure AI Search admin key")
    parser.add_argument("--embedding-model", default=None, help="Embedding model/deployment name")
    parser.add_argument("--chunk-chars", type=int, default=2200, help="Chunk size in characters")
    parser.add_argument("--overlap-chars", type=int, default=250, help="Chunk overlap in characters")
    parser.add_argument("--embed-batch-size", type=int, default=16, help="Embedding batch size")
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
    search_admin_key = args.search_admin_key or os.getenv("AZURE_SEARCH_ADMIN_KEY")
    ai_agent_cfg = settings.get_ai_agent_config()
    index_name = args.index_name or os.getenv("AZURE_SEARCH_INDEX_NAME") or ai_agent_cfg.get("index_name")
    model_client = create_model_client(settings)

    embedding_model = (
        args.embedding_model
        or os.getenv("AI_GATEWAY_EMBEDDING_MODEL")
        or ai_agent_cfg.get("embedding_model")
    )

    if not args.local_path and not args.url_file:
        LOGGER.error("At least one input source is required: --local-path and/or --url-file")
        return 1

    documents = _collect_local_documents(args.local_path, args.include_glob)
    documents.extend(_collect_url_documents(args.url_file, args.http_timeout))

    if not documents:
        LOGGER.warning("No documents collected. Nothing to ingest.")
        return 0

    records = _build_chunk_records(
        documents,
        chunk_chars=args.chunk_chars,
        overlap_chars=args.overlap_chars,
    )

    LOGGER.info("Collected %d documents, generated %d chunks", len(documents), len(records))

    if not embedding_model:
        LOGGER.error(
            "Missing embedding model configuration. Provide --embedding-model "
            "or set AI_GATEWAY_EMBEDDING_MODEL (or ai_agent.embedding_model)."
        )
        return 1
    if not model_client.is_available():
        LOGGER.error("APIM model client unavailable. Configure ai_agent.gateway_base_url and AI_GATEWAY_SUBSCRIPTION_KEY.")
        return 1

    try:
        LOGGER.info("Generating embeddings with model '%s'", embedding_model)
        _embed_records(
            records=records,
            model_client=model_client,
            embedding_model=embedding_model,
            batch_size=args.embed_batch_size,
        )

        if args.dry_run:
            LOGGER.info("Dry run enabled. Embeddings generated; skipping upload.")
            return 0

        if not search_endpoint or not search_admin_key or not index_name:
            LOGGER.error("Missing search configuration. Provide endpoint/key/index via args or env vars.")
            return 1

        LOGGER.info("Uploading %d chunks to index '%s'", len(records), index_name)
        _upload_records(
            endpoint=search_endpoint,
            admin_key=search_admin_key,
            index_name=index_name,
            records=records,
            batch_size=args.upload_batch_size,
        )
    except Exception:
        LOGGER.exception("Ingestion failed")
        return 1

    LOGGER.info("✓ Ingestion complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
