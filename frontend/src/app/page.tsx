"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import type { VideoMetadata, ChatMessage } from "@/types";
import * as api from "@/services/api";
import URLInput from "@/components/URLInput";
import VideoCard from "@/components/VideoCard";
import ChatPanel from "@/components/ChatPanel";

export default function Page() {
  const [urls, setUrls] = useState<string[]>([]);
  const [videoData, setVideoData] = useState<Record<string, VideoMetadata>>({});
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isIngesting, setIsIngesting] = useState(false);
  const [isChatLoading, setIsChatLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [indexingIds, setIndexingIds] = useState<Set<string>>(new Set());

  const sessionId = useRef<string>(crypto.randomUUID());
  const videoDataRef = useRef(videoData);

  // keep a ref so handleChat always reads the current video set without being in its deps
  videoDataRef.current = videoData;

  const hasVideos = Object.keys(videoData).length > 0;
  const isIndexing = indexingIds.size > 0;

  // poll /ingest/status until all pending videos are indexed
  useEffect(() => {
    if (indexingIds.size === 0) return;
    const ids = [...indexingIds];
    const timer = setInterval(async () => {
      try {
        const status = await api.getIngestStatus(ids);
        const pending = Object.entries(status)
          .filter(([, v]) => v !== "ready")
          .map(([k]) => k);
        setIndexingIds(new Set(pending));
        if (pending.length === 0) clearInterval(timer);
      } catch {
        // ignore poll errors, will retry on next tick
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [indexingIds]);

  const handleIngest = useCallback(async () => {
    const validUrls = urls.filter((u) => u.trim() !== "");
    if (validUrls.length === 0) return;

    setIsIngesting(true);
    setError(null);

    try {
      const result = await api.ingestVideos(validUrls);
      // merge new videos into existing set (additive)
      setVideoData((prev) => ({ ...prev, ...result.videos }));
      // mark all returned videos as potentially indexing; first poll will clear ready ones
      setIndexingIds((prev) => {
        const next = new Set(prev);
        for (const id of Object.keys(result.videos)) next.add(id);
        return next;
      });
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
    api.deleteVideo(videoId).catch(() => {});
  }, []);

  const handleNewChat = useCallback(() => {
    sessionId.current = crypto.randomUUID();
    setMessages([]);
    setVideoData({});
    setIndexingIds(new Set());
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
        <div className="flex items-center justify-between gap-4 border-b border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700">
          <span>{error}</span>
          <button
            type="button"
            onClick={() => setError(null)}
            aria-label="Dismiss error"
            className="shrink-0 rounded p-0.5 transition-colors hover:bg-red-100"
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
              <VideoCard label="Video 1" data={null} loading={true} />
              <VideoCard label="Video 2" data={null} loading={true} />
            </div>
          ) : hasVideos ? (
            <>
              <div className="flex shrink-0 items-center justify-between px-0.5">
                <span className="text-xs text-zinc-400">
                  {Object.keys(videoData).length} video{Object.keys(videoData).length !== 1 ? "s" : ""} loaded
                </span>
              </div>
              <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                {Object.entries(videoData).map(([key, meta], i) => (
                  <VideoCard
                    key={key}
                    label={`Video ${i + 1}`}
                    data={meta}
                    loading={false}
                    indexing={indexingIds.has(meta.video_id)}
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
        </aside>

        <section className="min-w-0 flex-1 overflow-hidden">
          <ChatPanel
            messages={messages}
            onSend={handleChat}
            onNewChat={handleNewChat}
            disabled={!hasVideos || isChatLoading || isIndexing}
            isLoading={isChatLoading}
          />
        </section>
      </main>
    </div>
  );
}
