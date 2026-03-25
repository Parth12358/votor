import os
import json
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Generator

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from votor.providers import embed_texts
from votor.db import get_collection, upsert_chunks, delete_file_chunks, delete_all, make_id
from votor.chunker import chunk_file, LANGUAGE_MAP

console = Console()

CONFIG_FILE  = Path(".vectormind/config.json")
HASH_FILE    = Path(".vectormind/file_hashes.json")
META_FILE    = Path(".vectormind/index_meta.json")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {
        "embedding_model": "text-embedding-3-small",
        "chunk_size":      200,
        "chunk_overlap":   20,
        "extensions": [
            ".py", ".js", ".ts", ".jsx", ".tsx",
            ".java", ".cpp", ".c", ".h", ".cs",
            ".go", ".rs", ".rb", ".php",
            ".md", ".txt", ".json", ".yaml", ".yml",
            ".toml", ".env.example"
        ],
        "exclude_dirs": [
            ".vectormind", ".git", "node_modules",
            "__pycache__", ".venv", "venv",
            "dist", "build", ".next", "votor.egg-info"
        ]
    }


# ---------------------------------------------------------------------------
# File hashing
# ---------------------------------------------------------------------------

def get_file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def load_hashes() -> dict:
    if HASH_FILE.exists():
        with open(HASH_FILE) as f:
            return json.load(f)
    return {}


def save_hashes(hashes: dict):
    with open(HASH_FILE, "w") as f:
        json.dump(hashes, f, indent=2)


# ---------------------------------------------------------------------------
# File crawling
# ---------------------------------------------------------------------------

def crawl_files(root: str, config: dict) -> Generator[Path, None, None]:
    """Walk project directory and yield files matching configured extensions."""
    extensions   = set(config["extensions"])
    exclude_dirs = set(config["exclude_dirs"])
    root_path    = Path(root).resolve()

    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [
            d for d in dirnames
            if d not in exclude_dirs and not d.startswith(".")
        ]

        for filename in filenames:
            filepath = Path(dirpath) / filename
            if filepath.suffix in extensions:
                yield filepath


# ---------------------------------------------------------------------------
# Main indexer
# ---------------------------------------------------------------------------

def index_project(
    root: str = ".",
    config: dict = None,
    incremental: bool = True,
    force: bool = False
) -> dict:
    """
    Crawl project, chunk files, embed, and store in Qdrant.

    Args:
        root:        Project root to index
        config:      Config dict (loaded from file if not provided)
        incremental: Only re-index changed files
        force:       Wipe entire DB and re-index everything

    Returns:
        Stats dict with files, chunks, updated, skipped counts
    """
    if config is None:
        config = load_config()

    chunk_size    = config.get("chunk_size", 200)
    chunk_overlap = config.get("chunk_overlap", 20)

    client, _ = get_collection()

    # Full wipe if forced
    if force:
        console.print("[dim]Wiping existing index...[/dim]")
        delete_all(client)
        hashes = {}
    else:
        hashes = load_hashes() if incremental else {}

    files_processed = 0
    files_skipped   = 0
    total_chunks    = 0
    new_hashes      = {}

    files = list(crawl_files(root, config))

    if not files:
        console.print("[yellow]No files found to index.[/yellow]")
        return {"files": 0, "chunks": 0, "updated": 0, "skipped": 0}

    with Progress(
        SpinnerColumn(style="#00ff9d"),
        TextColumn("[dim]{task.description}[/dim]"),
        BarColumn(complete_style="#00ff9d", finished_style="#00ff9d"),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task("Indexing...", total=len(files))

        for filepath in files:
            try:
                rel_path     = str(filepath.relative_to(Path(root).resolve()))
                current_hash = get_file_hash(filepath)
                new_hashes[rel_path] = current_hash

                # Skip unchanged files in incremental mode
                if incremental and not force and hashes.get(rel_path) == current_hash:
                    files_skipped += 1
                    progress.advance(task)
                    continue

                text = filepath.read_text(encoding="utf-8", errors="ignore")
                if not text.strip():
                    progress.advance(task)
                    continue

                chunks = chunk_file(
                    text=text,
                    extension=filepath.suffix,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    rel_path=rel_path
                )
                if not chunks:
                    progress.advance(task)
                    continue

                # Option B hook — summarize chunks before embedding (not implemented)
                # chunks = summarize_chunks(chunks, config)

                # Embed all chunks for this file
                embeddings = embed_texts(chunks, config)

                # Build IDs using stable hash
                ids = [make_id(rel_path, i) for i in range(len(chunks))]

                # Build metadata
                lang_name = LANGUAGE_MAP.get(filepath.suffix, "text")
                metadatas = [
                    {
                        "file":        rel_path,
                        "chunk_index": i,
                        "extension":   filepath.suffix,
                        "language":    lang_name,
                        "indexed_at":  datetime.utcnow().isoformat()
                    }
                    for i in range(len(chunks))
                ]

                # Remove old chunks before upserting
                if incremental and not force:
                    delete_file_chunks(client, rel_path)

                upsert_chunks(
                    client=client,
                    ids=ids,
                    embeddings=embeddings,
                    documents=chunks,
                    metadatas=metadatas
                )

                files_processed += 1
                total_chunks    += len(chunks)

            except Exception as e:
                console.print(f"[dim]Skipping {filepath.name}: {e}[/dim]")

            finally:
                progress.advance(task)

    # Save hashes
    if force:
        save_hashes(new_hashes)
    elif incremental:
        hashes.update(new_hashes)
        save_hashes(hashes)
    else:
        save_hashes(new_hashes)

    # Write index metadata
    meta = {
        "last_indexed": datetime.utcnow().isoformat(),
        "total_files":  files_processed + files_skipped,
        "total_chunks": total_chunks,
        "root":         str(Path(root).resolve())
    }
    with open(META_FILE, "w") as f:
        json.dump(meta, f, indent=2)

    return {
        "files":   files_processed,
        "chunks":  total_chunks,
        "updated": files_processed,
        "skipped": files_skipped,
    }