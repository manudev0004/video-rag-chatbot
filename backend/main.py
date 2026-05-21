import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sse_starlette.sse import EventSourceResponse

from .models.schemas import ChatRequest, ChatResponse, IngestRequest, IngestResponse, VideoMetadata
from .monitoring import probe_host, recent_metrics, system_info
from .services import cache_service
from .services.ingestion_service import chunk_transcript, get_collection, reset_collection, store_chunks, embed_query
from .services.metadata_service import get_video_metadata, reset_yt_service
from .services.rag_service import app as rag_app, get_llm, retrieve_context
from .services.transcript_service import extract_video_id, fetch_ydlp_info, get_transcript, is_youtube_url

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Video RAG Chatbot")

_allowed_origins = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

video_metadata_store: dict[str, dict] = {}
chat_histories: dict[str, list] = {}

_https_re = re.compile(r"^https?://")
_bad_scheme_re = re.compile(r"^h[a-z]*:?//")

ingest_counter = Counter("ingest_requests_total", "Total ingest requests")
chat_counter = Counter("chat_requests_total", "Total chat requests")
chat_latency = Histogram("chat_latency_seconds", "Chat response latency in seconds")


@app.get("/health")
def health() -> dict:
    """Return service status and number of loaded videos."""
    return {"status": "ok", "videos_loaded": len(video_metadata_store)}


def _normalize_url(url: str) -> str:
    url = url.strip()
    if not _https_re.match(url):
        url = _bad_scheme_re.sub("https://", url)
    return url


def _transcribe_and_embed_video(video_id: str, url: str) -> None:
    """Fetch the transcript then embed. Runs in the background after metadata is returned."""
    try:
        transcript = get_transcript(url)
    except Exception as exc:
        logger.error("Transcript fetch failed for video_id=%s: %s", video_id, exc)
        cache_service.mark_failed(video_id)
        return
    cache_service.save_transcript(video_id, transcript)
    _embed_video(video_id)


def _embed_video(video_id: str) -> None:
    """Chunk the transcript and store embeddings in ChromaDB."""
    entry = cache_service.load(video_id)
    if not entry:
        logger.warning("Background embed: no cache entry for video_id=%s", video_id)
        return
    transcript = entry.get("transcript")
    if not transcript:
        logger.warning("Background embed: no transcript in cache for video_id=%s", video_id)
        cache_service.mark_failed(video_id)
        return
    chunks = chunk_transcript(transcript, entry["metadata"])
    try:
        store_chunks(chunks)
        cache_service.mark_embedded(video_id)
        logger.info("Background embed done for video_id=%s", video_id)
    except Exception as exc:
        logger.error("Background embed failed for video_id=%s: %s", video_id, exc)
        cache_service.mark_failed(video_id)


def _process_one_url(
    raw_url: str, bg: BackgroundTasks
) -> tuple[str, str | None, dict | None, str | None]:
    """Handle one URL. Returns (original_url, video_id, metadata, error)."""
    url = _normalize_url(raw_url)
    try:
        video_id = extract_video_id(url)
    except ValueError as exc:
        return raw_url, None, None, str(exc)

    if cache_service.has_metadata(video_id):
        entry = cache_service.load(video_id)
        if not entry:
            return raw_url, None, None, f"Cache read failed for {video_id}"
        metadata = entry["metadata"]
        if not metadata.get("source_url"):
            metadata["source_url"] = url
        already_embedded = cache_service.is_embedded(video_id)
        if already_embedded:
            col = get_collection()
            if not col.get(where={"video_id": video_id}, limit=1, include=[])["ids"]:
                already_embedded = False
        if not already_embedded:
            if entry.get("transcript"):
                bg.add_task(_embed_video, video_id)
            else:
                bg.add_task(_transcribe_and_embed_video, video_id, url)
    else:
        if is_youtube_url(url):
            # metadata is quick (YouTube API), transcript can take a while (AssemblyAI)
            # so we return metadata straight away and do the rest in the background
            try:
                metadata = get_video_metadata(url)
            except (ValueError, RuntimeError, OSError) as exc:
                return raw_url, None, None, str(exc)
            if metadata.get("video_id") and metadata["video_id"] != video_id:
                video_id = metadata["video_id"]
            metadata["source_url"] = url
            cache_service.save_metadata_only(video_id, metadata)
            bg.add_task(_transcribe_and_embed_video, video_id, url)
        else:
            # for instagram/facebook yt-dlp handles both, reuse the same extract_info call
            try:
                info = fetch_ydlp_info(url)
            except RuntimeError as exc:
                return raw_url, None, None, str(exc)
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    transcript_fut = pool.submit(get_transcript, url, info)
                    metadata_fut = pool.submit(get_video_metadata, url, info)
                    transcript = transcript_fut.result()
                    metadata = metadata_fut.result()
            except (ValueError, RuntimeError, OSError) as exc:
                return raw_url, None, None, str(exc)
            if metadata.get("video_id") and metadata["video_id"] != video_id:
                video_id = metadata["video_id"]
            metadata["source_url"] = url
            cache_service.save(video_id, metadata, transcript)
            bg.add_task(_embed_video, video_id)

    return raw_url, video_id, metadata, None


def _process_one_url_safe(
    raw_url: str, bg: BackgroundTasks
) -> tuple[str, str | None, dict | None, str | None]:
    """Thin wrapper that retries once on unexpected failures."""
    try:
        return _process_one_url(raw_url, bg)
    except Exception as exc:
        logger.warning("Retrying %s: %s", raw_url, exc)
    try:
        return _process_one_url(raw_url, bg)
    except Exception as exc:
        logger.error("Ingest failed for %s after retry: %s", raw_url, exc)
        return raw_url, None, None, str(exc)


@app.post("/ingest", response_model=IngestResponse)
def ingest(request: IngestRequest, bg: BackgroundTasks) -> IngestResponse:
    """Return video metadata immediately; embedding runs in the background."""
    ingest_counter.inc()
    ingested: dict[str, VideoMetadata] = {}
    errors: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=len(request.urls) or 1) as pool:
        futures = [pool.submit(_process_one_url_safe, url, bg) for url in request.urls]
        for fut in futures:
            orig_url, video_id, metadata, error = fut.result()
            if error:
                errors[orig_url] = error
                continue
            video_metadata_store[video_id] = metadata
            ingested[video_id] = VideoMetadata(**metadata)
            logger.info("Ingest returned metadata for video_id=%s", video_id)

    return IngestResponse(status="ok", videos=ingested, errors=errors)


@app.get("/ingest/status")
def ingest_status(video_ids: str) -> dict:
    """Return 'ready', 'indexing', or 'failed' for each requested video_id."""
    ids = [v.strip() for v in video_ids.split(",") if v.strip()]
    result = {}
    for vid in ids:
        if cache_service.is_failed(vid):
            result[vid] = "failed"
        elif cache_service.is_embedded(vid):
            result[vid] = "ready"
        else:
            result[vid] = "indexing"
    return result


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """Run a RAG question over the loaded videos and return the full answer."""
    active_ids = request.video_ids or list(video_metadata_store.keys())
    if not active_ids:
        raise HTTPException(status_code=400, detail="No videos loaded. Call /ingest first.")

    chat_counter.inc()
    history = chat_histories.get(request.session_id, [])

    with chat_latency.time():
        result = rag_app.invoke({
            "question": request.question,
            "chat_history": history,
            "video_ids": active_ids,
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
    video_ids = request.video_ids if request.video_ids else list(video_metadata_store.keys())
    if not video_ids:
        raise HTTPException(status_code=400, detail="No videos loaded. Call /ingest first.")
    history = chat_histories.get(request.session_id, [])

    query_embedding = embed_query(request.question)
    all_contexts: list[str] = []
    raw_sources: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(video_ids)) as pool:
        futures = [pool.submit(retrieve_context, vid, query_embedding) for vid in video_ids]
        for fut in futures:
            context, sources = fut.result()
            all_contexts.append(context)
            raw_sources.extend(sources)

    seen: set[tuple[str, int]] = set()
    all_sources: list[dict] = []
    for s in raw_sources:
        key = (s["video_id"], s["chunk_index"])
        if key not in seen:
            seen.add(key)
            all_sources.append(s)

    context_blocks = "\n\n".join(
        f"--- Video {i + 1} ---\n{ctx}" for i, ctx in enumerate(all_contexts)
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
    messages.extend(history)
    messages.append(HumanMessage(content=request.question))

    llm = get_llm()

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


@app.delete("/videos/{video_id}")
def delete_video(video_id: str) -> dict:
    """Remove a video from the current session. Deletes its ChromaDB chunks and cache entry."""
    if video_id not in video_metadata_store:
        raise HTTPException(status_code=404, detail=f"Video not loaded: {video_id}")
    del video_metadata_store[video_id]
    col = get_collection()
    existing = col.get(where={"video_id": video_id}, include=[])
    if existing["ids"]:
        col.delete(ids=existing["ids"])
    cache_service.delete(video_id)
    return {"status": "ok", "video_id": video_id}


@app.delete("/videos")
def reset_videos() -> dict:
    """Remove all loaded videos, their ChromaDB chunks, and their cache entries."""
    video_metadata_store.clear()
    reset_collection()
    reset_yt_service()
    for vid in cache_service.list_ids():
        cache_service.delete(vid)
    return {"status": "ok"}


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
