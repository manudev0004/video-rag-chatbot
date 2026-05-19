import json
import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sse_starlette.sse import EventSourceResponse

from .models.schemas import ChatRequest, ChatResponse, IngestRequest, IngestResponse, VideoMetadata
from .monitoring import probe_host, recent_metrics, system_info
from .services.ingestion_service import chunk_transcript, store_chunks
from .services.metadata_service import get_video_metadata
from .services.rag_service import app as rag_app, retrieve_context
from .services.transcript_service import get_transcript

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Video RAG Chatbot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# stored in memory for now will swap for Redis in production
video_metadata_store: dict[str, dict] = {}
chat_histories: dict[str, list] = {}

ingest_counter = Counter("ingest_requests_total", "Total ingest requests")
chat_counter = Counter("chat_requests_total", "Total chat requests")
chat_latency = Histogram("chat_latency_seconds", "Chat response latency in seconds")


@app.get("/health")
def health() -> dict:
    """Return service status and number of loaded videos."""
    return {"status": "ok", "videos_loaded": len(video_metadata_store)}


@app.post("/ingest", response_model=IngestResponse)
def ingest(request: IngestRequest) -> IngestResponse:
    """Fetch, embed, and store all provided videos. Overwrites existing data for the same video IDs."""
    ingest_counter.inc()
    ingested: dict[str, VideoMetadata] = {}

    for url in request.urls:
        try:
            transcript = get_transcript(url)
            metadata = get_video_metadata(url)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        chunks = chunk_transcript(transcript, {
            "video_id": metadata["video_id"],
            "title": metadata["title"],
            "creator": metadata["creator"],
            "engagement_rate": metadata["engagement_rate"],
        })

        try:
            store_chunks(chunks)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Embedding failed: {exc}")

        video_metadata_store[metadata["video_id"]] = metadata
        ingested[metadata["video_id"]] = VideoMetadata(**metadata)
        logger.info("Ingested video_id=%s", metadata["video_id"])

    return IngestResponse(status="ok", videos=ingested)


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """Run a RAG question over the loaded videos and return the full answer."""
    if not video_metadata_store:
        raise HTTPException(status_code=400, detail="No videos loaded. Call /ingest first.")

    chat_counter.inc()
    history = chat_histories.get(request.session_id, [])

    with chat_latency.time():
        result = rag_app.invoke({
            "question": request.question,
            "chat_history": history,
            "video_ids": list(video_metadata_store.keys()),
            "contexts": [],
            "sources": [],
            "answer": "",
        })

    updated_history = history + [
        HumanMessage(content=request.question),
        AIMessage(content=result["answer"]),
    ]
    chat_histories[request.session_id] = updated_history[-10:]

    return ChatResponse(answer=result["answer"], sources=result["sources"])


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> EventSourceResponse:
    """Stream the LLM answer token by token over SSE, then emit sources and done events."""
    if not video_metadata_store:
        raise HTTPException(status_code=400, detail="No videos loaded. Call /ingest first.")

    video_ids = list(video_metadata_store.keys())
    history = chat_histories.get(request.session_id, [])

    all_contexts: list[str] = []
    all_sources: list[dict] = []
    for video_id in video_ids:
        context, sources = retrieve_context(video_id, request.question)
        all_contexts.append(context)
        all_sources.extend(sources)

    context_blocks = "\n\n".join(
        f"--- Video {i + 1} ---\n{ctx}" for i, ctx in enumerate(all_contexts)
    )
    system_content = (
        "You are a social media analyst comparing YouTube videos.\n"
        "Use only the context provided below to answer the question.\n"
        "Be specific and cite evidence from the transcripts.\n\n"
        + context_blocks
    )
    messages = [SystemMessage(content=system_content)]
    messages.extend(history)
    messages.append(HumanMessage(content=request.question))

    llm = ChatGroq(model=os.getenv("LLM_MODEL"), temperature=0, streaming=True)

    async def token_generator():
        full_answer = ""
        async for chunk in llm.astream(messages):
            token = chunk.content
            if token:
                full_answer += token
                yield {"event": "token", "data": token}

        yield {"event": "sources", "data": json.dumps(all_sources)}
        yield {"event": "done", "data": ""}

        updated_history = history + [
            HumanMessage(content=request.question),
            AIMessage(content=full_answer),
        ]
        chat_histories[request.session_id] = updated_history[-10:]

    return EventSourceResponse(token_generator())


@app.get("/metadata")
def get_metadata() -> dict:
    """Return metadata for all currently loaded videos."""
    return video_metadata_store


@app.delete("/session/{session_id}")
def delete_session(session_id: str) -> dict:
    """Clear the chat history for a session."""
    if session_id not in chat_histories:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    del chat_histories[session_id]
    return {"status": "ok", "session_id": session_id}


@app.get("/benchmark")
def benchmark() -> dict:
    """Return hardware info, network latency probes, and per-operation metrics from this process."""
    info = system_info()

    network_targets = [
        ("api.groq.com", 443),
        ("generativelanguage.googleapis.com", 443),
        ("www.youtube.com", 443),
    ]
    network = {host: probe_host(host, port) for host, port in network_targets}

    ops = []
    for m in recent_metrics():
        ops.append({
            "name": m.name,
            "duration_ms": m.duration_ms,
            "efficiency_score": m.efficiency_score,
            "bottleneck_hint": m.bottleneck_hint,
            "mem_delta_mb": m.mem_delta_mb,
            "success": m.success,
        })

    avg_score = round(sum(o["efficiency_score"] for o in ops) / len(ops), 1) if ops else None

    bottlenecks = [o["bottleneck_hint"] for o in ops if o["bottleneck_hint"] != "none"]
    top_bottleneck = max(set(bottlenecks), key=bottlenecks.count) if bottlenecks else "none"

    return {
        "system": info,
        "network_ms": network,
        "operations": ops,
        "summary": {
            "avg_efficiency_score": avg_score,
            "top_bottleneck": top_bottleneck,
            "ops_recorded": len(ops),
        },
    }


@app.get("/metrics")
def metrics() -> Response:
    """Expose Prometheus metrics."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
