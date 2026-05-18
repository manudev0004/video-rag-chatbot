# Video RAG Chatbot

A full-stack RAG system for creators to compare two YouTube videos through
conversational AI. 

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

## Architecture


## Stack
- Frontend: Next.js 14 (App Router, TypeScript)
- Backend: FastAPI (Python 3.11)
- Orchestration: LangGraph
- Embeddings: OpenAI text-embedding-3-small
- Vector DB: ChromaDB (local) → Qdrant (production)
- LLM: Groq / Llama-3.3-70b