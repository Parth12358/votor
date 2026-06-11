import atexit
import json
import hashlib
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue,
)

QDRANT_DIR  = Path(".vectormind/qdrant")
COLLECTION  = "votor"
META_FILE   = Path(".vectormind/index_meta.json")
CONFIG_FILE = Path(".vectormind/config.json")

VECTOR_SIZES = {
    "text-embedding-3-small":  1536,
    "text-embedding-3-large":  3072,
    "text-embedding-ada-002":  1536,
    "voyage-code-2":           1024,
    "nomic-embed-text":         768,
    "mxbai-embed-large":       1024,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_id(file_path: str, chunk_index: int) -> int:
    """Generate stable int ID from file path + chunk index."""
    raw = f"{file_path}::chunk_{chunk_index}"
    return int(hashlib.md5(raw.encode()).hexdigest(), 16) % (2 ** 63)


def get_vector_size(config: dict) -> int:
    model = config.get("embedding_model", "text-embedding-3-small")
    return VECTOR_SIZES.get(model, 1536)


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {"embedding_model": "text-embedding-3-small"}


# ---------------------------------------------------------------------------
# Client + Collection
# ---------------------------------------------------------------------------

_client: Optional[QdrantClient] = None
_client_lock = threading.Lock()


def get_client() -> QdrantClient:
    global _client
    with _client_lock:
        if _client is None:
            QDRANT_DIR.mkdir(parents=True, exist_ok=True)
            _client = QdrantClient(path=str(QDRANT_DIR))
        return _client


def close_client():
    """Close the cached Qdrant client to release the storage lock."""
    global _client
    with _client_lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
            _client = None


atexit.register(close_client)


def get_or_create_collection(client: QdrantClient, vector_size: int):
    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION not in collections:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(
                size=vector_size,
                distance=Distance.COSINE
            )
        )
    return client.get_collection(COLLECTION)


def get_collection():
    """Returns (client, collection_info)."""
    config = load_config()
    vector_size = get_vector_size(config)
    client = get_client()
    collection = get_or_create_collection(client, vector_size)
    return client, collection


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

def upsert_chunks(
    client: QdrantClient,
    ids: list,
    embeddings: list,
    documents: list,
    metadatas: list
):
    if not ids:
        return

    points = [
        PointStruct(
            id=ids[i],
            vector=embeddings[i],
            payload={**metadatas[i], "text": documents[i]}
        )
        for i in range(len(ids))
    ]
    client.upsert(collection_name=COLLECTION, points=points)


def delete_file_chunks(client: QdrantClient, file_path: str):
    try:
        client.delete(
            collection_name=COLLECTION,
            points_selector=Filter(
                must=[FieldCondition(key="file", match=MatchValue(value=file_path))]
            )
        )
    except Exception:
        pass


def delete_all(client: QdrantClient):
    try:
        client.delete_collection(COLLECTION)
    except Exception:
        pass
    config = load_config()
    get_or_create_collection(client, get_vector_size(config))


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------

def query_chunks(
    client: QdrantClient,
    query_embedding: list,
    top_k: int = 5,
    file_filter: Optional[str] = None
) -> dict:
    query_filter = None
    if file_filter:
        query_filter = Filter(
            must=[FieldCondition(key="file", match=MatchValue(value=file_filter))]
        )

    from qdrant_client.models import QueryRequest
    results = client.query_points(
        collection_name=COLLECTION,
        query=query_embedding,
        query_filter=query_filter,
        limit=top_k,
        with_payload=True
    ).points

    documents, metadatas, scores = [], [], []
    for r in results:
        payload = r.payload or {}
        documents.append(payload.get("text", ""))
        scores.append(round(r.score, 4))
        metadatas.append({k: v for k, v in payload.items() if k != "text"})

    return {"documents": documents, "metadatas": metadatas, "scores": scores}


def list_indexed_files(client: QdrantClient) -> list:
    try:
        results, _ = client.scroll(
            collection_name=COLLECTION,
            with_payload=["file"],
            limit=100000
        )
        files = {r.payload["file"] for r in results if r.payload and "file" in r.payload}
        return sorted(files)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def get_stats() -> dict:
    try:
        client = get_client()
        info = client.get_collection(COLLECTION)
        total_chunks = info.points_count
        files = list_indexed_files(client)
        total_files = len(files)
    except Exception:
        total_chunks = 0
        total_files  = 0
        files        = []

    last_indexed = "never"
    if META_FILE.exists():
        with open(META_FILE) as f:
            meta = json.load(f)
            last_indexed = meta.get("last_indexed", "never")

    return {
        "total_chunks": total_chunks,
        "total_files":  total_files,
        "last_indexed": last_indexed,
        "files":        files
    }