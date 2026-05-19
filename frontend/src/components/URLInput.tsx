"use client";

import { KeyboardEvent, ClipboardEvent } from "react";

interface URLInputProps {
  urls: string[];
  onChange: (urls: string[]) => void;
  onSubmit: () => void;
  loading: boolean;
}

const INITIAL_ROWS = 2;

function normalizeRows(urls: string[]): string[] {
  return urls.length === 0 ? Array(INITIAL_ROWS).fill("") : urls;
}

export default function URLInput({ urls, onChange, onSubmit, loading }: URLInputProps) {
  const rows = normalizeRows(urls);
  const hasContent = rows.some((u) => u.trim() !== "");

  function update(index: number, value: string) {
    const next = [...rows];
    next[index] = value;
    onChange(next);
  }

  function addRow() {
    onChange([...rows, ""]);
  }

  function removeRow(index: number) {
    onChange(rows.filter((_, i) => i !== index));
  }

  function handlePaste(e: ClipboardEvent<HTMLInputElement>, index: number) {
    const pasted = e.clipboardData.getData("text");
    const lines = pasted.split("\n").map((l) => l.trim()).filter(Boolean);
    if (lines.length <= 1) return;

    e.preventDefault();
    const next = [...rows];
    next.splice(index, 1, ...lines);
    onChange(next);
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && !e.shiftKey && hasContent && !loading) {
      e.preventDefault();
      onSubmit();
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {rows.map((url, i) => (
        <div key={i} className="flex items-center gap-2">
          <input
            type="url"
            value={url}
            placeholder="https://www.youtube.com/watch?v=..."
            onChange={(e) => update(i, e.target.value)}
            onPaste={(e) => handlePaste(e, i)}
            onKeyDown={handleKeyDown}
            disabled={loading}
            className="flex-1 rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
          />
          {rows.length > 1 && (
            <button
              type="button"
              onClick={() => removeRow(i)}
              disabled={loading}
              aria-label="Remove URL"
              className="rounded-md px-2 py-2 text-zinc-400 hover:text-red-500 disabled:opacity-40 transition-colors"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
              </svg>
            </button>
          )}
        </div>
      ))}

      <div className="flex items-center justify-between gap-3 pt-1">
        <button
          type="button"
          onClick={addRow}
          disabled={loading}
          className="text-sm text-blue-600 hover:text-blue-700 disabled:opacity-40 transition-colors"
        >
          + Add Video
        </button>

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
