"use client";

import { useState, useRef, useEffect, useCallback, KeyboardEvent } from "react";
import Markdown from "react-markdown";
import type { Components } from "react-markdown";
import type { ChatMessage } from "@/types";

const mdComponents: Components = {
  p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="mb-2 ml-4 list-disc space-y-0.5">{children}</ul>,
  ol: ({ children }) => <ol className="mb-2 ml-4 list-decimal space-y-0.5">{children}</ol>,
  li: ({ children }) => <li>{children}</li>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  h1: ({ children }) => <h1 className="mb-1 text-base font-semibold">{children}</h1>,
  h2: ({ children }) => <h2 className="mb-1 text-sm font-semibold">{children}</h2>,
  h3: ({ children }) => <h3 className="mb-1 text-sm font-semibold">{children}</h3>,
  code: ({ children }) => (
    <code className="rounded bg-zinc-200 px-1 py-0.5 font-mono text-xs">{children}</code>
  ),
};

interface ChatPanelProps {
  messages: ChatMessage[];
  onSend: (text: string) => void;
  onNewChat?: () => void;
  disabled: boolean;
  isLoading: boolean;
  videoCount: number;
}

const singleVideoSuggestions = [
  "What is this video mainly about?",
  "What are the key takeaways from this video?",
  "How does the creator present their argument?",
  "Who is the target audience for this video?",
];

const multiVideoSuggestions = [
  "What is each video mainly about?",
  "How do the videos differ in tone and style?",
  "Which video has better audience engagement and why?",
  "Summarize the key takeaways across all the videos.",
];

function SourceList({ sources }: { sources: Array<Record<string, unknown>> }) {
  if (sources.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1">
      {sources.map((src, i) => {
        const label = typeof src.title === "string" ? src.title : `Source ${i + 1}`;
        return (
          <span
            key={i}
            className="max-w-[200px] truncate rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-500"
            title={label}
          >
            {label}
          </span>
        );
      })}
    </div>
  );
}

function StreamingDots() {
  return (
    <span className="inline-flex items-center gap-0.5 pl-1">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="h-1.5 w-1.5 rounded-full bg-zinc-400 animate-bounce"
          style={{ animationDelay: `${i * 0.15}s` }}
        />
      ))}
    </span>
  );
}

export default function ChatPanel({ messages, onSend, onNewChat, disabled, isLoading, videoCount }: ChatPanelProps) {
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const suggestions = videoCount >= 2 ? multiVideoSuggestions : singleVideoSuggestions;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const submit = useCallback(() => {
    const text = input.trim();
    if (!text || disabled) return;
    setInput("");
    onSend(text);
  }, [input, disabled, onSend]);

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  function resizeTextarea(el: HTMLTextAreaElement) {
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 144)}px`;
  }

  const lastMessage = messages[messages.length - 1];
  const isStreaming = lastMessage?.role === "assistant" && lastMessage?.isStreaming === true;

  return (
    <div className="flex h-full flex-col rounded-xl border border-zinc-200 bg-white shadow-sm">
      <div className="flex shrink-0 items-center justify-between border-b border-zinc-100 px-4 py-2.5">
        <span className="text-xs font-medium text-zinc-400">Chat</span>
        {messages.length > 0 && onNewChat && (
          <button
            type="button"
            onClick={onNewChat}
            className="flex items-center gap-1 text-xs text-zinc-400 hover:text-zinc-700"
          >
            <svg className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" clipRule="evenodd" />
            </svg>
            New chat
          </button>
        )}
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-4 py-8">
            <p className="text-sm text-zinc-400">Ask anything about the videos</p>
            <div className="grid grid-cols-1 gap-2 w-full max-w-sm">
              {suggestions.map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => onSend(q)}
                  disabled={disabled}
                  className="rounded-lg border border-zinc-200 px-3 py-2 text-left text-sm text-zinc-600 hover:bg-zinc-50 disabled:opacity-40 transition-colors"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => {
          const isUser = msg.role === "user";
          const isLast = i === messages.length - 1;

          return (
            <div key={i} className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[80%] ${isUser ? "items-end" : "items-start"} flex flex-col`}>
                <div
                  className={`rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                    isUser
                      ? "rounded-tr-sm bg-blue-600 text-white whitespace-pre-wrap"
                      : "rounded-tl-sm bg-zinc-100 text-zinc-900"
                  }`}
                >
                  {isUser ? (
                    msg.content
                  ) : (
                    <>
                      <Markdown components={mdComponents}>{msg.content}</Markdown>
                      {isLast && isStreaming && <StreamingDots />}
                    </>
                  )}
                </div>

                {!isUser && msg.sources && msg.sources.length > 0 && (
                  <SourceList sources={msg.sources} />
                )}
              </div>
            </div>
          );
        })}

        {!isStreaming && lastMessage?.role === "assistant" && (
          <div className="flex flex-wrap gap-2 pt-2">
            {suggestions.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => onSend(q)}
                disabled={disabled}
                className="rounded-lg border border-zinc-200 px-3 py-1.5 text-left text-xs text-zinc-600 hover:bg-zinc-50 disabled:opacity-40 transition-colors"
              >
                {q}
              </button>
            ))}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      <div className="border-t border-zinc-200 px-4 py-3">
        <div className="flex items-end gap-2">
          <textarea
            rows={1}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              resizeTextarea(e.target);
            }}
            onKeyDown={handleKeyDown}
            disabled={disabled}
            placeholder="Ask a question..."
            className="flex-1 resize-none overflow-y-auto rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
          />
          <button
            type="button"
            onClick={submit}
            disabled={disabled || !input.trim()}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors shrink-0"
          >
            {isLoading ? "..." : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}
