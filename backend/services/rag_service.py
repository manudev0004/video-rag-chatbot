import os
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypedDict

from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage

from .ingestion_service import embed_query, search_chunks
from ..monitoring import track

load_dotenv()
logger = logging.getLogger(__name__)


class VideoAnalysisState(TypedDict):
    question: str
    chat_history: list[Any]
    video_ids: list[str]
    contexts: list[str]
    sources: list[dict]
    answer: str


@track("retrieve_context")
def retrieve_context(video_id: str, query_embedding: list[float], k: int = 4) -> tuple[str, list[dict]]:
    """Search ChromaDB for relevant chunks from a specific video.

    Returns the context string and a list of source metadata dicts.
    """
    matched_chunks = search_chunks(query_embedding, video_id=video_id, k=k)

    if not matched_chunks:
        logger.warning("No chunks found for video_id=%s", video_id)
        return "", []

    # Prepend video-level metadata so the LLM sees title and engagement rate
    video_meta = matched_chunks[0]["metadata"]
    header = (
        f"Video: {video_meta['title']} by {video_meta['creator']}\n"
        f"Engagement Rate: {video_meta['engagement_rate']}\n\n"
    )
    context = header + "\n\n".join(chunk["text"] for chunk in matched_chunks)

    sources = [
        {
            "video_id": chunk["metadata"]["video_id"],
            "title": chunk["metadata"]["title"],
            "chunk_index": chunk["metadata"]["chunk_index"],
            "distance": round(chunk["distance"], 4),
        }
        for chunk in matched_chunks
    ]

    logger.info("Retrieved %d chunks for video_id=%s", len(matched_chunks), video_id)
    return context, sources


def retrieve_contexts(state: VideoAnalysisState) -> dict:
    """LangGraph node: fetch context for all videos."""
    query_embedding = embed_query(state["question"])
    video_ids = state["video_ids"]
    contexts, sources = [], []

    with ThreadPoolExecutor(max_workers=len(video_ids) or 1) as pool:
        futures = [pool.submit(retrieve_context, vid, query_embedding) for vid in video_ids]
        for fut in futures:
            context, video_sources = fut.result()
            contexts.append(context)
            sources.extend(video_sources)

    # one source entry per video, keep the closest chunk
    best: dict[str, dict] = {}
    for s in sources:
        vid = s["video_id"]
        if vid not in best or s["distance"] < best[vid]["distance"]:
            best[vid] = s

    return {"contexts": contexts, "sources": list(best.values())}


_llm: ChatGroq | None = None


def get_llm() -> ChatGroq:
    """Return the shared ChatGroq instance, creating it on first call."""
    global _llm
    if _llm is None:
        _llm = ChatGroq(model=os.getenv("LLM_MODEL"), temperature=0, streaming=True)
    return _llm


def generate_answer(state: VideoAnalysisState) -> dict:
    """LangGraph node: generate an answer from the retrieved context."""
    llm = get_llm()

    context_blocks = "\n\n".join(
        f"--- Video {i + 1} ---\n{ctx}" for i, ctx in enumerate(state["contexts"])
    )
    system_content = (
        "You are a social media video analyst. Answer the user's question using only "
        "the context provided below.\n\n"
        "Context structure: each numbered section covers one video. It includes the "
        "video title, creator, engagement rate (likes + comments as a share of views), "
        "and relevant transcript excerpts. If a section starts with [no transcript], "
        "that video had no captions — the content shown is built from its title, "
        "description, and tags instead.\n\n"
        "Rules:\n"
        "- Use only information from the context. Do not use outside knowledge about "
        "these videos, creators, or topics.\n"
        "- If a video is marked [no transcript], say so when discussing its content. "
        "Your understanding of what that video covers is limited to its metadata.\n"
        "- For comparison questions, address each video. If a video's context is "
        "empty or missing, say that rather than skipping it silently.\n"
        "- Back up your points with specific details from the transcripts: direct "
        "quotes, stats, or concrete examples.\n"
        "- If the context does not contain enough information to answer, say so "
        "clearly. Do not guess.\n"
        "- Be direct and concise. No filler, no restating the question.\n\n"
        + context_blocks
    )

    messages = [SystemMessage(content=system_content)]
    messages.extend(state["chat_history"])
    messages.append(HumanMessage(content=state["question"]))

    response = llm.invoke(messages)
    logger.info("Answer generated (%d chars)", len(response.content))
    return {"answer": response.content}


def _build_graph():
    graph = StateGraph(VideoAnalysisState)
    graph.add_node("retrieve_contexts", retrieve_contexts)
    graph.add_node("generate_answer", generate_answer)
    graph.set_entry_point("retrieve_contexts")
    graph.add_edge("retrieve_contexts", "generate_answer")
    graph.add_edge("generate_answer", END)
    return graph.compile()


app = _build_graph()
