import os
import re
import shutil
import threading
import time
import uuid
import logging

from dotenv import load_dotenv
import chromadb

from ..monitoring import track
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings

load_dotenv()
logger = logging.getLogger(__name__)

_chroma_client = None
_collection = None
_embeddings_model = None
_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
# Serializes Gemini embed calls to avoid free-tier quota exhaustion from concurrent tasks.
_embed_lock = threading.Lock()
# prevents two threads racing to create the same PersistentClient
_chroma_lock = threading.Lock()


def _get_chroma_client():
    global _chroma_client
    with _chroma_lock:
        if _chroma_client is None:
            _chroma_client = chromadb.PersistentClient(
                path=os.getenv("CHROMA_PATH", "./backend/chroma_db")
            )
    return _chroma_client


def get_embeddings_model() -> GoogleGenerativeAIEmbeddings:
    """Return the shared Gemini embeddings model, creating it on first call."""
    global _embeddings_model
    if _embeddings_model is None:
        model = os.getenv("EMBEDDING_MODEL")
        if not model:
            raise RuntimeError("EMBEDDING_MODEL is not set in the environment.")
        _embeddings_model = GoogleGenerativeAIEmbeddings(model=model)
    return _embeddings_model


def get_collection() -> chromadb.Collection:
    """Return the persistent ChromaDB collection for video chunks."""
    global _collection
    if _collection is None:
        client = _get_chroma_client()  # acquires and releases _chroma_lock
        with _chroma_lock:
            if _collection is None:
                _collection = client.get_or_create_collection("video_chunks")
    return _collection


def reset_collection() -> None:
    """Drop and recreate the collection, cleaning up orphaned segment folders.

    ChromaDB 1.5.x delete_collection() removes SQLite metadata but leaves UUID
    segment directories on disk. We delete those manually so the index starts clean.
    """
    global _collection
    client = _get_chroma_client()
    chroma_path = os.getenv("CHROMA_PATH", "./backend/chroma_db")
    try:
        client.delete_collection("video_chunks")
    except Exception:
        pass
    # Remove orphaned UUID segment folders left behind by delete_collection
    if os.path.exists(chroma_path):
        for entry in os.scandir(chroma_path):
            if entry.is_dir():
                shutil.rmtree(entry.path)
    _collection = client.get_or_create_collection("video_chunks")
    logger.info("ChromaDB collection reset: segment folders removed, fresh collection created")


def chunk_transcript(text: str, metadata: dict) -> list[dict]:
    """Split a transcript into overlapping chunks with metadata."""
    texts = _splitter.split_text(text)
    total = len(texts)

    base_meta: dict = {
        "video_id": metadata["video_id"],
        "title": metadata["title"],
        "creator": metadata["creator"],
        "engagement_rate": metadata["engagement_rate"],
    }
    # chromadb doesn't accept None - only add stats that are actually known
    for key in ("views", "likes", "comments", "subscriber_count"):
        val = metadata.get(key)
        if val is not None:
            base_meta[key] = int(val)

    return [
        {
            "id": str(uuid.uuid4()),
            "text": chunk,
            "metadata": {**base_meta, "chunk_index": i, "total_chunks": total},
        }
        for i, chunk in enumerate(texts)
    ]


def _embed_with_retry(model: GoogleGenerativeAIEmbeddings, texts: list[str], batch_index: int) -> list[list[float]]:
    """Call embed_documents with up to 3 retries on rate-limit errors.

    Holds _embed_lock so parallel store_chunks tasks don't hit the Gemini API
    simultaneously and exhaust the free-tier quota.
    """
    with _embed_lock:
        for attempt in range(3):
            try:
                return model.embed_documents(texts)
            except Exception as exc:
                msg = str(exc)
                if "RESOURCE_EXHAUSTED" not in msg and "429" not in msg:
                    raise
                if attempt == 2:
                    raise
                m = re.search(r"retryDelay.*?(\d+)s", msg)
                delay = int(m.group(1)) + 5 if m else 60
                logger.warning("Rate limit hit on batch %d, retrying in %ds (attempt %d/3)", batch_index, delay, attempt + 1)
            time.sleep(delay)
    raise RuntimeError("embed_with_retry: unreachable")


@track("store_chunks")
def store_chunks(chunks: list[dict]) -> int:
    """Embed and store chunks in ChromaDB, replacing any existing chunks for the same video."""
    if not chunks:
        return 0

    collection = get_collection()
    embeddings_model = get_embeddings_model()

    video_id = chunks[0]["metadata"]["video_id"]

    collection.delete(where={"video_id": video_id})

    batch_size = 100
    stored = 0
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        texts = [c["text"] for c in batch]
        embeddings = _embed_with_retry(embeddings_model, texts, batch_index=i)
        collection.add(
            ids=[c["id"] for c in batch],
            documents=texts,
            embeddings=embeddings,
            metadatas=[c["metadata"] for c in batch],
        )
        stored += len(batch)
        logger.info("Stored batch %d-%d", i, i + len(batch) - 1)

    return stored


def search_chunks(query_embedding: list[float], video_id: str | None = None, k: int = 4) -> list[dict]:
    """Return the top-k chunks matching a pre-computed query embedding."""
    collection = get_collection()
    where = {"video_id": video_id} if video_id else None

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    docs = results["documents"][0]
    if not docs:
        return []

    return [
        {"text": text, "metadata": metadata, "distance": distance}
        for text, metadata, distance in zip(docs, results["metadatas"][0], results["distances"][0])
    ]


def embed_query(query: str) -> list[float]:
    """Embed a query string using the configured Gemini model."""
    return get_embeddings_model().embed_query(query)
