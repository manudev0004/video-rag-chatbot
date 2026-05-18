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
    video_a_id: str
    video_b_id: str
    context_a: str
    context_b: str
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


def retrieve_video_a(state: VideoAnalysisState) -> dict:
    """LangGraph node: fetch context for video A."""
    context, sources = retrieve_context(state["video_a_id"], state["question"])
    return {"context_a": context, "sources": sources}


def retrieve_video_b(state: VideoAnalysisState) -> dict:
    """LangGraph node: fetch context for video B and merge sources."""
    context, sources = retrieve_context(state["video_b_id"], state["question"])
    return {"context_b": context, "sources": state["sources"] + sources}


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

    system_content = (
        "You are a social media analyst comparing two YouTube videos.\n"
        "Use only the context provided below to answer the question.\n"
        "Be specific and cite evidence from the transcripts.\n\n"
        f"--- Video A ---\n{state['context_a']}\n\n"
        f"--- Video B ---\n{state['context_b']}"
    )

    messages = [SystemMessage(content=system_content)]
    messages.extend(state["chat_history"])
    messages.append(HumanMessage(content=state["question"]))

    response = llm.invoke(messages)
    logger.info("Answer generated (%d chars)", len(response.content))
    return {"answer": response.content}


def _build_graph():
    graph = StateGraph(VideoAnalysisState)
    graph.add_node("retrieve_video_a", retrieve_video_a)
    graph.add_node("retrieve_video_b", retrieve_video_b)
    graph.add_node("generate_answer", generate_answer)
    graph.set_entry_point("retrieve_video_a")
    graph.add_edge("retrieve_video_a", "retrieve_video_b")
    graph.add_edge("retrieve_video_b", "generate_answer")
    graph.add_edge("generate_answer", END)
    return graph.compile()


app = _build_graph()
