# Video RAG Chatbot

A chatbot that takes video URLs and lets you compare them through conversation. You ask a question, it finds the relevant parts of each transcript and uses an LLM to answer based on what it actually found.

YouTube is fully supported, both long videos and Shorts. Instagram reels work too, long and short. Facebook has basic support for videos and reels but metadata like follower counts and likes is still patchy, so treat that as a work in progress.

Built with FastAPI on the backend, Next.js on the frontend, and LangGraph to handle the retrieval and generation steps.

Live at: https://video-rag-chatbot.vercel.app/
Backend: https://video-rag-79ecbd35845a.herokuapp.com/

## Status
Working. Backend and frontend are connected, end-to-end flow runs.


## Running

Clone the repo and set up dependencies first:

```bash
git clone https://github.com/your-username/video-rag-chatbot.git
cd video-rag-chatbot
pip install -r backend/requirements.txt
```

Copy `.env.example` to `.env` and fill in your API keys:

```
YOUTUBE_API_KEY=
GOOGLE_API_KEY=
GROQ_API_KEY=
ASSEMBLYAI_API_KEY=
```

Start the backend from the project root (port 8000):

```bash
uvicorn backend.main:app --reload --port 8000
```

Start the frontend:

```bash
cd frontend && npm install && npm run dev
```

Backend docs at `http://localhost:8000/docs`, frontend at `http://localhost:3000`.

## How it works

You paste video URLs and hit analyze.

For YouTube, metadata comes back from the YouTube Data API straight away so the card shows up fast. The transcript fetch runs in the background. It tries `youtube_transcript_api` first since it is quick and free. The problem is cloud hosts like Heroku get their IPs flagged by YouTube fairly often, so that call gets blocked without warning. When that happens, the app tries to extract the raw audio stream and send it to AssemblyAI for transcription. AssemblyAI fetches and transcribes the audio from their own servers, so your server's IP being blocked does not matter. Getting the audio URL itself is also not always straightforward from a datacenter IP, so there are two extractors for that step: yt-dlp first, then pytubefix as a backup. If both fail, the app falls back to a `[no transcript]` placeholder built from the video title, description and tags so the video still indexes and shows up. Nothing ever blocks the ingest flow completely.

For Instagram, yt-dlp handles both the transcript and the metadata in a single info fetch, so the network call only happens once. Long reels and short reels both work. If the reel is behind a login wall, the app reads Instagram session cookies from the `INSTAGRAM_COOKIES` environment variable and writes them to a temporary file that yt-dlp reads at request time. This was necessary because Instagram started blocking unauthenticated yt-dlp requests on a lot of content. You can also set `YOUTUBE_COOKIES` the same way to pass YouTube session cookies, which helps when the transcript API is blocked and you want to improve the chance of yt-dlp getting through. Storing cookies in env vars rather than a file in the repo keeps them out of version control.

For Facebook, the same yt-dlp path works for getting transcripts from videos and reels. However, Facebook's page structure makes it hard to reliably extract engagement stats like reactions and follower counts. The current code makes a best effort but it breaks on some page layouts. Full Facebook metadata support is still being worked on.

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

If a video has no subtitles and the audio path also fails, the system builds a fallback from the title, description, and tags and marks it as `[no transcript]` in the context. The LLM is told to flag this when discussing that video so the answer is honest about the limitation. In the worst case, where even yt-dlp info is unavailable, it uses a minimal placeholder with just the video ID so the card still shows up and the chat can at least acknowledge the video exists.

## Embedding rate limits

Gemini's free tier has a per-minute quota. To avoid hitting it when multiple videos are indexing at the same time, embedding calls are serialized through a lock. Only one video embeds at a time. Each batch also retries up to 3 times on a 429, waiting however long the API says to wait before trying again.

## Sessions

Chat history is saved to `localStorage` so past conversations show up in the sidebar. Resuming a session reloads the video metadata from the stored data and re-ingests the videos in the background so you can pick up where you left off without starting from scratch.

You can also add or remove videos during an ongoing chat. Deleting a video removes its chunks from ChromaDB and the card from the UI. Adding a new one mid-chat goes through the same ingest flow and becomes available once indexing finishes. The chat input stays disabled until all loaded videos are ready.

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
- Transcript (YouTube): youtube-transcript-api, then yt-dlp audio + AssemblyAI, then pytubefix audio + AssemblyAI, then metadata placeholder
- Transcript (Instagram, Facebook): yt-dlp subtitles with AssemblyAI as fallback when no captions exist
- Speech-to-text: AssemblyAI (used when subtitles are unavailable or the transcript API is blocked)
- Audio extraction: yt-dlp and pytubefix (YouTube), yt-dlp (Instagram, Facebook)


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
