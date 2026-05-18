import os
import logging
from typing import Any, TypedDict

from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage

from .ingestion_service import get_collection, get_embeddings_model

load_dotenv()
logger = logging.getLogger(__name__)


class VideoAnalysisState(TypedDict):
    question: str
    chat_history: list[Any]
    video_ids: list[str]
    contexts: list[str]
    sources: list[dict]
    answer: str


def retrieve_context(video_id: str, query: str, k: int = 4) -> tuple[str, list[dict]]:
    """Search ChromaDB for relevant chunks from a specific video.

    Returns the formatted context string and a list of source metadata dicts.
    """
    embeddings_model = get_embeddings_model()
    query_embedding = embeddings_model.embed_query(query)

    collection = get_collection()
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=k,
        where={"video_id": video_id},
        include=["documents", "metadatas", "distances"],
    )

    docs = results["documents"][0]
    metas = results["metadatas"][0]
    distances = results["distances"][0]

    if not docs:
        logger.warning("No chunks found for video_id=%s", video_id)
        return "", []

    # Prepend video-level metadata so the LLM sees title and engagement rate
    first = metas[0]
    header = (
        f"Video: {first['title']} by {first['creator']}\n"
        f"Engagement Rate: {first['engagement_rate']}\n\n"
    )
    context = header + "\n\n".join(docs)

    sources = [
        {
            "video_id": m["video_id"],
            "title": m["title"],
            "chunk_index": m["chunk_index"],
            "distance": round(d, 4),
        }
        for m, d in zip(metas, distances)
    ]

    logger.info("Retrieved %d chunks for video_id=%s", len(docs), video_id)
    return context, sources


def retrieve_contexts(state: VideoAnalysisState) -> dict:
    """LangGraph node: fetch context for all videos."""
    contexts, sources = [], []
    for video_id in state["video_ids"]:
        context, srcs = retrieve_context(video_id, state["question"])
        contexts.append(context)
        sources.extend(srcs)
    return {"contexts": contexts, "sources": sources}


def get_llm() -> ChatGroq:
    """Return a configured ChatGroq instance."""
    return ChatGroq(
        model=os.getenv("LLM_MODEL"),
        temperature=0,
        streaming=False,
    )


def generate_answer(state: VideoAnalysisState) -> dict:
    """LangGraph node: compare both videos and produce the final answer."""
    llm = get_llm()

    context_blocks = "\n\n".join(
        f"--- Video {i + 1} ---\n{ctx}" for i, ctx in enumerate(state["contexts"])
    )
    system_content = (
        "You are a social media analyst comparing YouTube videos.\n"
        "Use only the context provided below to answer the question.\n"
        "Be specific and cite evidence from the transcripts.\n\n"
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
