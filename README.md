# Video RAG Chatbot

A chatbot that takes YouTube video URLs and lets you compare them through conversation. You ask a question, it finds the relevant parts of each transcript and uses an LLM to answer based on what it actually found.

Built with FastAPI on the backend, Next.js on the frontend, and LangGraph to handle the retrieval and generation steps. 

## Status
🔨 In development

## Notebooks

These are the building blocks I prototyped before wiring everything together. Each one was a checkpoint to make sure that piece actually worked.

**transcript_experiments.ipynb**
Pulls raw transcripts from YouTube using `youtube_transcript_api`. Handles different URL formats and cleans up the text. Tested on a few real videos and it works clean.

**video_metadata_analysis.ipynb**
Fetches real stats from the YouTube Data API: views, likes, comments, duration and engagement rate. This is what gives the chatbot something concrete to compare beyond just transcript content.

**chunking_and_embeddings.ipynb**
Splits transcripts into overlapping chunks and embeds them into ChromaDB using Gemini. Got the Veritasium video stored at chunks and semantic search is returning the right results.

**rag_pipeline_prototype.ipynb**
The full RAG pipeline with LangGraph. Two retrieval nodes pull context for each video separately, then Groq's Llama model generates the comparison. Follow-up questions work too since chat history lives in the state.

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

Add keys in `.env` file in the project root:

## How it works

You paste YouTube URLs and hit analyze. The backend fetches the transcript via `youtube_transcript_api` and pulls stats (views, likes, comments, engagement rate) from the YouTube Data API. Both happen in parallel using a thread pool so neither one blocks the other.

The transcript gets split into overlapping chunks of 1000 characters with 200 character overlap, each one embedded with Gemini and stored in ChromaDB tagged with the video ID.

When you ask a question, LangGraph runs two nodes in sequence. The first one queries ChromaDB separately for each video and grabs the top matching chunks. The second one builds a labeled context block per video and sends all of it to Groq's Llama model to generate the comparison. Tokens stream back over SSE so the answer starts appearing right away.

Chat history is kept per session and capped at the last 10 messages so the prompt does not grow unbounded.

## Caching and background embedding

The first time you load a video, metadata comes back in the API response immediately. Embedding runs as a background task so you are not staring at a loading screen waiting for the full indexing to finish.

The frontend polls `/ingest/status` every 2 seconds and shows an indexing indicator on each video card. The chat input stays disabled until all loaded videos are ready.

Once a video is fetched, it gets saved to disk under `backend/video_cache`. If you load the same video again it skips the YouTube API calls entirely. For a demo this matters a lot since you are probably loading the same videos repeatedly.

After embedding finishes, the raw transcript is dropped from the cache file. The text is already stored in ChromaDB as chunks so keeping a second copy on disk is just wasted space. The cache file only holds the metadata after that point.

## Retrieval

All video chunks live in a single ChromaDB collection. Each chunk has the video ID stored as metadata. When a question comes in, the search runs once per video with a filter on that video ID so it never scans chunks that belong to a different video.

The source shown in the chat is the chunk that was closest to your question for each video, picked by distance score.

## Monitoring

The `/benchmark` endpoint returns hardware info, network latency probes to the external APIs (Groq, Gemini, YouTube), and a history of recent operations with timing and memory delta. Each operation gets an efficiency score based on how fast it ran vs how loaded the machine was at that moment. Useful for figuring out whether something is slow because of the code or because the server was already busy.

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
- Vector DB: ChromaDB (local)
- LLM: Groq, Llama-3.3-70b-versatile
