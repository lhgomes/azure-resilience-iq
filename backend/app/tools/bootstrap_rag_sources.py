#!/usr/bin/env python
"""Bootstrap unified Terraform modules RAG sources for Azure AI Search ingestion.

This script creates the local folder structure, writes curated URL files,
clones official AVM/CAF repositories, and exports markdown docs into
backend/agent/rag/terraform/docs.

Usage:
  python -m app.tools.bootstrap_rag_sources

  python -m app.tools.bootstrap_rag_sources --refresh-clone

  python -m app.tools.bootstrap_rag_sources --skip-clone
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List


@dataclass(frozen=True)
class RepoSource:
    name: str
    url: str


OFFICIAL_REPOS: List[RepoSource] = [
    RepoSource(
        name="Azure-Verified-Modules",
        url="https://github.com/Azure/Azure-Verified-Modules.git",
    ),
    RepoSource(
        name="terraform-azurerm-caf-enterprise-scale",
        url="https://github.com/Azure/terraform-azurerm-caf-enterprise-scale.git",
    ),
]


TERRAFORM_MODULE_URLS: List[str] = [
    "https://azure.github.io/Azure-Verified-Modules/",
    "https://github.com/Azure/Azure-Verified-Modules",
    "https://aka.ms/avm",
    "https://learn.microsoft.com/azure/cloud-adoption-framework/",
    "https://learn.microsoft.com/azure/cloud-adoption-framework/ready/landing-zone/",
    "https://github.com/Azure/terraform-azurerm-caf-enterprise-scale",
]


def _run(command: List[str], cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=str(cwd) if cwd else None, check=True)


def _ensure_dirs(base_rag_dir: Path) -> None:
    (base_rag_dir / "terraform" / "source-repos").mkdir(parents=True, exist_ok=True)
    (base_rag_dir / "terraform" / "docs").mkdir(parents=True, exist_ok=True)


def _write_urls(url_file: Path, urls: Iterable[str]) -> None:
    deduped: List[str] = []
    seen = set()
    for value in urls:
        text = value.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)

    url_file.parent.mkdir(parents=True, exist_ok=True)
    url_file.write_text("\n".join(deduped) + "\n", encoding="utf-8")


def _clone_repo(repo: RepoSource, source_root: Path, refresh_clone: bool) -> Path:
    destination = source_root / repo.name
    if refresh_clone and destination.exists():
        shutil.rmtree(destination)

    if not destination.exists():
        _run(["git", "clone", "--depth", "1", repo.url, str(destination)])
    else:
        _run(["git", "-C", str(destination), "pull", "--ff-only"])

    return destination


def _is_skipped_path(path: Path) -> bool:
    skipped_segments = {
        ".git",
        ".github",
        ".devcontainer",
        ".terraform",
        "node_modules",
        "vendor",
    }
    return any(segment in skipped_segments for segment in path.parts)


def _export_markdown(repo_root: Path, export_root: Path) -> int:
    if export_root.exists():
        shutil.rmtree(export_root)
    export_root.mkdir(parents=True, exist_ok=True)

    exported = 0
    for path in repo_root.rglob("*.md"):
        if not path.is_file() or _is_skipped_path(path):
            continue

        relative = path.relative_to(repo_root)
        destination = export_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        exported += 1

    return exported


def _default_repo_root() -> Path:
    # backend/app/tools/bootstrap_rag_sources.py -> repo root at parents[3]
    return Path(__file__).resolve().parents[3]


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap unified Terraform modules RAG source folders and URLs")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root (defaults to auto-detected workspace root)",
    )
    parser.add_argument(
        "--base-rag-dir",
        type=Path,
        default=None,
        help="Base RAG data dir (defaults to backend/agent/rag)",
    )
    parser.add_argument(
        "--refresh-clone",
        action="store_true",
        help="Delete existing clones before cloning again",
    )
    parser.add_argument(
        "--skip-clone",
        action="store_true",
        help="Skip clone/pull step",
    )
    parser.add_argument(
        "--skip-export",
        action="store_true",
        help="Skip markdown export step",
    )
    parser.add_argument(
        "--skip-urls",
        action="store_true",
        help="Skip URL file generation",
    )
    args = parser.parse_args()

    repo_root = (args.repo_root or _default_repo_root()).resolve()
    backend_root = repo_root / "backend"
    base_rag_dir = (args.base_rag_dir or (backend_root / "agent" / "rag")).resolve()

    _ensure_dirs(base_rag_dir)
    terraform_root = base_rag_dir / "terraform"

    if not args.skip_urls:
        _write_urls(terraform_root / "terraform_urls.txt", TERRAFORM_MODULE_URLS)
        print(f"✓ Wrote URL list: {terraform_root / 'terraform_urls.txt'}")

    for repo in OFFICIAL_REPOS:
        source_root = terraform_root / "source-repos"
        docs_root = terraform_root / "docs" / repo.name

        if args.skip_clone:
            clone_path = source_root / repo.name
            if not clone_path.exists():
                print(f"! Skipping clone for {repo.name}, but path does not exist: {clone_path}")
                continue
        else:
            clone_path = _clone_repo(repo, source_root, refresh_clone=args.refresh_clone)
            print(f"✓ Synced repo: {repo.name} -> {clone_path}")

        if not args.skip_export:
            count = _export_markdown(clone_path, docs_root)
            print(f"✓ Exported {count} markdown files to {docs_root}")

    print("✓ Terraform modules RAG bootstrap complete")
    print(f"  Base dir: {base_rag_dir}")
    print("  Use with ingest script:")
    print(
        "  python -m app.llm.ingest_search "
        "--targets terraform "
        f"--terraform-local-path {terraform_root / 'docs'} "
        f"--terraform-url-file {terraform_root / 'terraform_urls.txt'} "
        "--terraform-index-name learn-terraform-index"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
