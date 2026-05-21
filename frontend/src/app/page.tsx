"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import type { VideoMetadata, ChatMessage, ChatSession } from "@/types";
import * as api from "@/services/api";
import URLInput from "@/components/URLInput";
import VideoCard from "@/components/VideoCard";
import ChatPanel from "@/components/ChatPanel";
import SessionList from "@/components/SessionList";

export default function Page() {
  const [urls, setUrls] = useState<string[]>([]);
  const [videoData, setVideoData] = useState<Record<string, VideoMetadata>>({});
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isIngesting, setIsIngesting] = useState(false);
  const [isChatLoading, setIsChatLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [indexingIds, setIndexingIds] = useState<Set<string>>(new Set());
  const [failedIds, setFailedIds] = useState<Set<string>>(new Set());
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string>(() => crypto.randomUUID());

  const sessionId = useRef<string>(currentSessionId);
  const videoDataRef = useRef(videoData);

  videoDataRef.current = videoData;

  const videoCount = Object.keys(videoData).length;
  const hasVideos = videoCount > 0;
  const isIndexing = indexingIds.size > 0;

  useEffect(() => {
    try {
      const raw = localStorage.getItem("rag-sessions");
      if (raw) setSessions(JSON.parse(raw) as ChatSession[]);
    } catch {
      // ignore, data is corrupted
    }
  }, []);

  useEffect(() => {
    localStorage.setItem("rag-sessions", JSON.stringify(sessions));
  }, [sessions]);

  useEffect(() => {
    if (messages.length === 0) return;
    const last = messages[messages.length - 1];
    if (last?.isStreaming || last?.role !== "assistant") return;

    const sid = currentSessionId;
    const firstUserMsg = messages.find((m) => m.role === "user");
    const title = (firstUserMsg?.content.slice(0, 40) ?? "Chat").trim();
    const currentVideos = videoDataRef.current;

    setSessions((prev) => {
      const idx = prev.findIndex((s) => s.id === sid);
      const updated: ChatSession = {
        id: sid,
        title,
        messages: messages.map((m) => ({ ...m, isStreaming: false })),
        videoIds: Object.keys(currentVideos),
        videoData: { ...currentVideos },
        createdAt: idx >= 0 ? prev[idx].createdAt : Date.now(),
      };
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = updated;
        return next;
      }
      return [updated, ...prev];
    });
  }, [messages, currentSessionId]);

  useEffect(() => {
    if (indexingIds.size === 0) return;
    let active = true;
    const ids = [...indexingIds];
    const timer = setInterval(async () => {
      try {
        const status = await api.getIngestStatus(ids);
        if (!active) return;
        const pending = Object.entries(status)
          .filter(([, v]) => v === "indexing")
          .map(([k]) => k);
        const nowFailed = Object.entries(status)
          .filter(([, v]) => v === "failed")
          .map(([k]) => k);
        setIndexingIds(new Set(pending));
        if (nowFailed.length > 0) {
          setFailedIds((prev) => new Set([...prev, ...nowFailed]));
          setError("Embedding failed for one or more videos. You can delete and re-add them.");
        }
        if (pending.length === 0) clearInterval(timer);
      } catch {
        // ignore poll errors, will retry on next tick
      }
    }, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [indexingIds]);

  const handleIngest = useCallback(async () => {
    const validUrls = urls.filter((u) => u.trim() !== "");
    if (validUrls.length === 0) return;

    setIsIngesting(true);
    setError(null);

    try {
      const result = await api.ingestVideos(validUrls);
      setVideoData((prev) => ({ ...prev, ...result.videos }));
      const returnedIds = Object.keys(result.videos);
      if (returnedIds.length > 0) {
        const status = await api.getIngestStatus(returnedIds);
        const needsIndexing = returnedIds.filter((id) => status[id] === "indexing");
        if (needsIndexing.length > 0) {
          setIndexingIds((prev) => {
            const next = new Set(prev);
            for (const id of needsIndexing) next.add(id);
            return next;
          });
        }
      }
      if (Object.keys(result.errors).length > 0) {
        const lines = Object.values(result.errors);
        setError(lines.join("\n"));
      }
    } catch (err) {
      setError(
        err instanceof Error
          ? `Failed to ingest videos: ${err.message}`
          : "Failed to ingest videos. Please try again."
      );
    } finally {
      setIsIngesting(false);
    }
  }, [urls]);

  const handleDeleteVideo = useCallback((videoId: string) => {
    setVideoData((prev) => {
      const next = { ...prev };
      delete next[videoId];
      return next;
    });
    setIndexingIds((prev) => {
      const next = new Set(prev);
      next.delete(videoId);
      return next;
    });
    setFailedIds((prev) => {
      const next = new Set(prev);
      next.delete(videoId);
      return next;
    });
    api.deleteVideo(videoId).catch(() => {});
  }, []);

  const handleNewChat = useCallback(() => {
    const newId = crypto.randomUUID();
    sessionId.current = newId;
    setCurrentSessionId(newId);
    setMessages([]);
    setVideoData({});
    setIndexingIds(new Set());
    setFailedIds(new Set());
    setUrls([]);
    setError(null);
  }, []);

  const handleResumeSession = useCallback(async (session: ChatSession) => {
    sessionId.current = session.id;
    setCurrentSessionId(session.id);
    setMessages(session.messages);
    setVideoData(session.videoData);
    setIndexingIds(new Set());
    setFailedIds(new Set());
    setUrls([]);
    setError(null);

    if (session.videoIds.length === 0) return;

    const videoUrls = Object.values(session.videoData)
      .map((m) => m.source_url || `https://www.youtube.com/watch?v=${m.video_id}`)
      .filter(Boolean);
    setIsIngesting(true);
    try {
      const result = await api.ingestVideos(videoUrls);
      setVideoData((prev) => ({ ...prev, ...result.videos }));
      const resumedIds = Object.keys(result.videos);
      if (resumedIds.length > 0) {
        const status = await api.getIngestStatus(resumedIds);
        const needsIndexing = resumedIds.filter((id) => status[id] === "indexing");
        if (needsIndexing.length > 0) {
          setIndexingIds(new Set(needsIndexing));
        }
      }
    } catch (err) {
      setError(
        err instanceof Error
          ? `Could not reload session videos: ${err.message}`
          : "Could not reload session videos."
      );
    } finally {
      setIsIngesting(false);
    }
  }, []);

  const handleDeleteSession = useCallback((sid: string) => {
    setSessions((prev) => prev.filter((s) => s.id !== sid));
    api.deleteSession(sid).catch(() => {});
  }, []);

  const handleReset = useCallback(() => {
    api.resetVideos().catch(() => {});
    const newId = crypto.randomUUID();
    sessionId.current = newId;
    setCurrentSessionId(newId);
    setMessages([]);
    setVideoData({});
    setIndexingIds(new Set());
    setFailedIds(new Set());
    setSessions([]);
    setUrls([]);
    setError(null);
  }, []);

  const handleChat = useCallback(async (question: string) => {
    const userMessage: ChatMessage = { role: "user", content: question };
    const assistantMessage: ChatMessage = { role: "assistant", content: "", isStreaming: true };

    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setIsChatLoading(true);
    setError(null);

    try {
      await api.streamChat(
        question,
        sessionId.current,
        Object.keys(videoDataRef.current),
        (token) => {
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              next[next.length - 1] = { ...last, content: last.content + token };
            }
            return next;
          });
        },
        (sources) => {
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              next[next.length - 1] = { ...last, sources };
            }
            return next;
          });
        },
        () => {
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              next[next.length - 1] = { ...last, isStreaming: false };
            }
            return next;
          });
          setIsChatLoading(false);
        }
      );
    } catch (err) {
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last?.role === "assistant") {
          next[next.length - 1] = { ...last, isStreaming: false };
        }
        return next;
      });
      setError(
        err instanceof Error
          ? `Chat error: ${err.message}`
          : "Something went wrong with the chat. Please try again."
      );
      setIsChatLoading(false);
    }
  }, []);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-zinc-50">
      {error && (
        <div className="flex items-start justify-between gap-3 border-b border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700">
          <ul className="max-h-[72px] overflow-y-auto space-y-0.5 py-0.5 min-w-0">
            {error.split("\n").map((line, i) => (
              <li key={i} className="truncate" title={line}>{line}</li>
            ))}
          </ul>
          <button
            type="button"
            onClick={() => setError(null)}
            aria-label="Dismiss error"
            className="mt-0.5 shrink-0 rounded p-0.5 transition-colors hover:bg-red-100"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </button>
        </div>
      )}

      <header className="shrink-0 border-b border-zinc-200 bg-white px-6 py-4">
        <URLInput
          urls={urls}
          onChange={setUrls}
          onSubmit={handleIngest}
          loading={isIngesting}
        />
      </header>

      <main className="flex flex-1 gap-4 overflow-hidden p-4">
        <aside className="flex w-[40%] shrink-0 flex-col gap-3 overflow-y-auto">
          {isIngesting && !hasVideos ? (
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              {urls.filter((u) => u.trim() !== "").map((_, i) => (
                <VideoCard key={i} label={`Video ${i + 1}`} data={null} loading={true} />
              ))}
            </div>
          ) : hasVideos ? (
            <>
              <div className="flex shrink-0 items-center justify-between px-0.5">
                <span className="text-xs text-zinc-400">
                  {videoCount} video{videoCount !== 1 ? "s" : ""} loaded
                </span>
                <button
                  type="button"
                  onClick={handleReset}
                  className="flex items-center gap-1 text-xs text-zinc-400 hover:text-red-500"
                  title="Clear all videos, chats, and history"
                >
                  <svg className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor">
                    <path fillRule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clipRule="evenodd" />
                  </svg>
                  Reset all
                </button>
              </div>
              <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                {Object.entries(videoData).map(([key, meta], i) => (
                  <VideoCard
                    key={key}
                    label={`Video ${i + 1}`}
                    data={meta}
                    loading={false}
                    indexing={indexingIds.has(meta.video_id)}
                    failed={failedIds.has(meta.video_id)}
                    onDelete={() => handleDeleteVideo(meta.video_id)}
                  />
                ))}
              </div>
            </>
          ) : (
            <div className="flex h-48 items-center justify-center rounded-xl border border-dashed border-zinc-300 text-sm text-zinc-400">
              Videos will appear here after ingestion
            </div>
          )}

          <SessionList
            sessions={sessions}
            activeId={currentSessionId}
            onResume={handleResumeSession}
            onDelete={handleDeleteSession}
          />
        </aside>

        <section className="min-w-0 flex-1 overflow-hidden">
          <ChatPanel
            messages={messages}
            onSend={handleChat}
            onNewChat={handleNewChat}
            disabled={!hasVideos || isChatLoading || isIndexing}
            isLoading={isChatLoading}
            videoCount={videoCount}
          />
        </section>
      </main>
    </div>
  );
}
