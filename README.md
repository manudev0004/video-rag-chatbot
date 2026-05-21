# Video RAG Chatbot

A chatbot that takes video URLs (YouTube, Instagram, Facebook) and lets you compare them through conversation. You ask a question, it finds the relevant parts of each transcript and uses an LLM to answer based on what it actually found.

Built with FastAPI on the backend, Next.js on the frontend, and LangGraph to handle the retrieval and generation steps. 

## Status
Working. Backend and frontend are connected, end-to-end flow runs.

## Notebooks

These were the building blocks before wiring everything together. Each one was a checkpoint to make sure that piece actually worked before porting it to the backend.

**transcript_experiments.ipynb**
Pulls raw transcripts from YouTube using `youtube_transcript_api`. Handles different URL formats and cleans up the text. Tested on a few real videos and it works clean.

**video_metadata_analysis.ipynb**
Fetches real stats from the YouTube Data API: views, likes, comments, duration and engagement rate. This is what gives the chatbot something concrete to compare beyond just transcript content.

**chunking_and_embeddings.ipynb**
Splits transcripts into overlapping chunks and embeds them into ChromaDB using Gemini. Got the Veritasium video stored at chunks and semantic search is returning the right results.

**rag_pipeline_prototype.ipynb**
The full RAG pipeline with LangGraph. A retrieval node queries ChromaDB for each video in parallel, then a generation node builds the comparison using Groq's Llama model. Follow-up questions work too since chat history lives in the graph state.

## Running

The FastAPI server runs on port **8000**. Start it from the project root:

```bash
uvicorn backend.main:app --reload --port 8000
```

Start the frontend:

```bash
cd frontend && npm install && npm run dev
```

Backend docs at `http://localhost:8000/docs`, frontend at `http://localhost:3000`.

Add keys in `.env` file in the project root (copy from `.env.example`).

## How it works

You paste video URLs and hit analyze. It works with YouTube, Instagram, and Facebook. For YouTube it fetches the transcript via `youtube_transcript_api` and pulls stats from the YouTube Data API. Both happen in parallel so neither one blocks the other.

For Instagram and Facebook, yt-dlp handles both the transcript and the metadata in a single info fetch. That single call is shared between both tasks so the network request only happens once, not twice.

The transcript gets split into overlapping chunks of 1000 characters with 200 character overlap, each one embedded with Gemini and stored in ChromaDB tagged with the video ID.

When you ask a question, LangGraph runs two nodes in sequence. The first node queries ChromaDB for each video in parallel and grabs the top matching chunks. The second node builds a labeled context block per video and sends all of it to Groq's Llama model. Tokens stream back over SSE so the answer starts appearing straight away.

Chat history is kept per session and capped at the last 10 messages so the prompt does not grow unbounded.

## Caching and background embedding

The first time you load a video, metadata comes back in the API response immediately. Embedding runs as a background task so you are not staring at a loading screen waiting for the full indexing to finish.

The frontend polls `/ingest/status` every 2 seconds and shows an indexing indicator on each video card. The chat input stays disabled until all loaded videos are ready.

Once a video is fetched, the transcript and metadata are saved to disk under `backend/video_cache`. If you load the same video again it skips the API calls entirely and goes straight to embedding. For a demo this matters since you are probably loading the same videos more than once.

After embedding finishes, the raw transcript is dropped from the cache file. The text is already stored in ChromaDB as chunks so keeping a second copy on disk is just wasted space. The cache file only holds the metadata after that point.

The cache keeps up to 20 videos on disk. Older ones get evicted automatically when that limit is hit.

## Retrieval

All video chunks live in a single ChromaDB collection. Each chunk has the video ID stored as metadata. When a question comes in, the search runs once per video with a filter on that video ID so it never scans chunks that belong to a different video.

The source shown in the chat is the chunk that was closest to your question for each video, picked by distance score.

If a video has no subtitles at all, the system builds a fallback from the title, description, and tags and marks it as `[no transcript]` in the context. The LLM is told to flag this when discussing that video so the answer is honest about the limitation.

## Embedding rate limits

Gemini's free tier has a per-minute quota. To avoid hitting it when multiple videos are indexing at the same time, embedding calls are serialized through a lock. Only one video embeds at a time. Each batch also retries up to 3 times on a 429, waiting however long the API says to wait before trying again.

## Sessions

Chat history is saved to `localStorage` so past conversations show up in the sidebar. Resuming a session reloads the video metadata from the stored data and re-ingests the videos in the background so you can pick up where you left off without starting from scratch.

## Monitoring

The `/benchmark` endpoint returns hardware info, network latency probes to the external APIs (Groq, Gemini, YouTube), and a log of recent operations with timing and memory usage. Each operation gets a score based on how fast it ran relative to how loaded the machine was. Useful for telling whether something is slow because of the code or because the host was already busy.

Prometheus metrics are exposed at `/metrics`.

## Endpoints

| Method | Path | What it does |
|--------|------|--------------|
| GET | /health | status and number of loaded videos |
| POST | /ingest | fetch transcripts, embed and store them |
| GET | /ingest/status | indexing status per video ID |
| POST | /chat | full RAG answer, non-streaming |
| POST | /chat/stream | same but streams tokens over SSE |
| GET | /metadata | metadata for all loaded videos |
| DELETE | /videos/{video_id} | remove a video and its ChromaDB chunks |
| DELETE | /videos | remove all videos and reset state |
| DELETE | /session/{session_id} | clear chat history for a session |
| GET | /benchmark | hardware, network and operation metrics |
| GET | /metrics | Prometheus metrics |

## Stack

- Frontend: Next.js 14 (App Router, TypeScript)
- Backend: FastAPI (Python 3.11)
- Orchestration: LangGraph
- Embeddings: Gemini gemini-embedding-001
- Vector DB: ChromaDB (local persistent)
- LLM: Groq, Llama-3.3-70b-versatile
- Media extraction: yt-dlp (Instagram, Facebook, YouTube fallback)
