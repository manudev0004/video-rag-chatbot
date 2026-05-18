import os
import uuid
import logging

from dotenv import load_dotenv
import chromadb
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings

load_dotenv()
logger = logging.getLogger(__name__)


def get_embeddings_model() -> GoogleGenerativeAIEmbeddings:
    """Return a configured Gemini embeddings model."""
    model = os.getenv("EMBEDDING_MODEL")
    if not model:
        raise RuntimeError("EMBEDDING_MODEL is not set in the environment.")
    return GoogleGenerativeAIEmbeddings(model=model)


def get_collection() -> chromadb.Collection:
    """Return the persistent ChromaDB collection for video chunks."""
    chroma_path = os.getenv("CHROMA_PATH", "./backend/chroma_db")
    client = chromadb.PersistentClient(path=chroma_path)
    return client.get_or_create_collection("video_chunks")


def chunk_transcript(text: str, metadata: dict) -> list[dict]:
    """Split a transcript into overlapping chunks with metadata."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
    )
    texts = splitter.split_text(text)
    total = len(texts)
    return [
        {
            "id": str(uuid.uuid4()),
            "text": chunk,
            "metadata": {
                "video_id": metadata["video_id"],
                "title": metadata["title"],
                "creator": metadata["creator"],
                "engagement_rate": metadata["engagement_rate"],
                "chunk_index": i,
                "total_chunks": total,
            },
        }
        for i, chunk in enumerate(texts)
    ]


def store_chunks(chunks: list[dict]) -> int:
    """Embed and store chunks in ChromaDB, replacing any existing chunks for the same video."""
    if not chunks:
        return 0

    collection = get_collection()
    embeddings_model = get_embeddings_model()

    video_id = chunks[0]["metadata"]["video_id"]

    existing = collection.get(where={"video_id": video_id}, include=[])
    if existing["ids"]:
        collection.delete(ids=existing["ids"])
        logger.info("Deleted %d existing chunks for video_id=%s", len(existing["ids"]), video_id)

    batch_size = 100
    stored = 0
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        texts = [c["text"] for c in batch]
        embeddings = embeddings_model.embed_documents(texts)
        collection.add(
            ids=[c["id"] for c in batch],
            documents=texts,
            embeddings=embeddings,
            metadatas=[c["metadata"] for c in batch],
        )
        stored += len(batch)
        logger.info("Stored batch %d-%d", i, i + len(batch) - 1)

    return stored


def search_chunks(query: str, video_id: str | None = None, k: int = 4) -> list[dict]:
    """Embed a query and return the top-k matching chunks from ChromaDB."""
    embeddings_model = get_embeddings_model()
    query_embedding = embeddings_model.embed_query(query)

    collection = get_collection()
    where = {"video_id": video_id} if video_id else None

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    hits = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        hits.append({"text": doc, "metadata": meta, "distance": dist})
    return hits
