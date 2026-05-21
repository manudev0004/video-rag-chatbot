"use client";

import { useState, KeyboardEvent } from "react";

interface URLInputProps {
  urls: string[];
  onChange: (urls: string[]) => void;
  onSubmit: () => void;
  loading: boolean;
}

const MAX_URLS = 10;

const DEMO_URLS = [
  "https://www.youtube.com/watch?v=ksn5yrsC3Wg",
  "https://www.youtube.com/watch?v=12DWqKQ6KHw",
  "https://youtu.be/B3m3AMRlYfc?si=z8YAE2kJR5p6GAMl",
  "https://youtu.be/jAHF7L_Fh9Q?si=C8uztfFSNswfkXlk",
  "https://youtu.be/TYhNHX372ek?si=zHy3QD8n3_VcxOPN",
];

export default function URLInput({ urls, onChange, onSubmit, loading }: URLInputProps) {
  const [demoCount, setDemoCount] = useState(2);

  const text = urls.join("\n");
  const hasContent = urls.some((u) => u.trim() !== "");

  function handleChange(value: string) {
    onChange(value.split("\n").slice(0, MAX_URLS));
  }

  function loadDemos() {
    onChange(DEMO_URLS.slice(0, demoCount));
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && e.ctrlKey && hasContent && !loading) {
      e.preventDefault();
      onSubmit();
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <textarea
        rows={3}
        value={text}
        onChange={(e) => handleChange(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={loading}
        placeholder={`One URL per line (YouTube, Instagram, Facebook), up to ${MAX_URLS}`}
        className="w-full resize-none rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
      />
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="text-xs text-zinc-400">Demo:</span>
          <button
            type="button"
            onClick={() => setDemoCount((c) => Math.max(1, c - 1))}
            disabled={loading || demoCount <= 1}
            className="rounded border border-zinc-200 px-2 py-0.5 text-xs text-zinc-600 hover:bg-zinc-50 disabled:opacity-40 transition-colors"
          >
            -
          </button>
          <span className="w-4 text-center text-xs text-zinc-700">{demoCount}</span>
          <button
            type="button"
            onClick={() => setDemoCount((c) => Math.min(DEMO_URLS.length, c + 1))}
            disabled={loading || demoCount >= DEMO_URLS.length}
            className="rounded border border-zinc-200 px-2 py-0.5 text-xs text-zinc-600 hover:bg-zinc-50 disabled:opacity-40 transition-colors"
          >
            +
          </button>
          <button
            type="button"
            onClick={loadDemos}
            disabled={loading}
            className="text-xs text-blue-600 hover:text-blue-700 disabled:opacity-40 transition-colors"
          >
            Load
          </button>
        </div>

        <button
          type="button"
          onClick={onSubmit}
          disabled={!hasContent || loading}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {loading ? "Analyzing..." : "Analyze Videos"}
        </button>
      </div>
    </div>
  );
}
