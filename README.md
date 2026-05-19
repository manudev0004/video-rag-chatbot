# Video RAG Chatbot

A full-stack RAG system for creators to compare two YouTube videos through conversational AI. 

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

## Backend

The FastAPI server runs on port **8000**. Start it from the project root:

```bash
uvicorn backend.main:app --reload --port 8000
```

then interactive docs are at `http://localhost:8000/docs`.

### Endpoints

| Method | Path | What it does |
|--------|------|--------------|
| GET | `/health` | quick check, returns status and how many videos are loaded |
| POST | `/ingest` | takes YouTube URLs, pulls transcripts, embeds and stores them in ChromaDB |
| POST | `/chat` | runs a RAG question over the loaded videos, returns the full answer |
| POST | `/chat/stream` | same as `/chat` but streams tokens over SSE |
| GET | `/metadata` | returns metadata for all currently loaded videos |
| DELETE | `/session/{session_id}` | clears the chat history for a session |
| GET | `/metrics` | Prometheus metrics |


### Order of operations

1. Start the backend (`uvicorn backend.main:app --reload --port 8000`)


## Architecture

The flow is pretty straightforward. You give it two YouTube URLs and it handles everything from there.

Ingestion pulls the transcript using `youtube_transcript_api`, then grabs the stats (views, likes, comments, duration, engagement rate) from the YouTube Data API. The transcript gets split into overlapping chunks, each one embedded with Gemini and stored in ChromaDB under that video's ID.

When a question comes in, LangGraph runs two nodes in sequence. The first one queries ChromaDB separately for each video and pulls the top matching chunks. The second one builds a labeled context block per video and sends all of it to Groq's Llama model to generate the comparison. For `/chat/stream`, tokens come back over SSE as they are generated so the UI feels responsive.

Chat history is stored per session in memory on the server. It keeps the last 10 messages so the model has enough context without the prompt getting too long.

## Stack
- Frontend: Next.js 14 (App Router, TypeScript)
- Backend: FastAPI (Python 3.11)
- Orchestration: LangGraph
- Embeddings: Gemini text-embedding-001
- Vector DB: ChromaDB (local)
- LLM: Groq / Llama-3.3-70b