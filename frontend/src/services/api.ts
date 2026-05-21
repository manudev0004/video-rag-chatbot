import type { IngestResponse, VideoMetadata, SourceChunk } from "@/types";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    let detail = text;
    try {
      const parsed = JSON.parse(text) as { detail?: string };
      if (parsed.detail) detail = parsed.detail.split("\n")[0].trim();
    } catch { /* not JSON, use raw text */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function ingestVideos(urls: string[]): Promise<IngestResponse> {
  const res = await fetch(`${BASE_URL}/ingest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ urls }),
  });
  return handleResponse<IngestResponse>(res);
}

export async function getMetadata(): Promise<Record<string, VideoMetadata>> {
  const res = await fetch(`${BASE_URL}/metadata`);
  return handleResponse<Record<string, VideoMetadata>>(res);
}

export async function getIngestStatus(videoIds: string[]): Promise<Record<string, string>> {
  const res = await fetch(`${BASE_URL}/ingest/status?video_ids=${videoIds.join(",")}`);
  return handleResponse<Record<string, string>>(res);
}

export async function deleteVideo(videoId: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/videos/${videoId}`, { method: "DELETE" });
  await handleResponse<unknown>(res);
}

export async function resetVideos(): Promise<void> {
  const res = await fetch(`${BASE_URL}/videos`, { method: "DELETE" });
  await handleResponse<unknown>(res);
}

export async function deleteSession(sessionId: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/session/${sessionId}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) await handleResponse<unknown>(res);
}

export async function streamChat(
  question: string,
  sessionId: string,
  videoIds: string[],
  onToken: (token: string) => void,
  onSources: (sources: SourceChunk[]) => void,
  onDone: () => void
): Promise<void> {
  const res = await fetch(`${BASE_URL}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, session_id: sessionId, video_ids: videoIds }),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`Chat request failed (${res.status}): ${text}`);
  }

  if (!res.body) {
    throw new Error("Response body is null, streaming is not supported");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  function handleEvent(block: string): boolean {
    let type = "";
    const lines: string[] = [];

    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) {
        type = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        const val = line.slice(5);
        // strip one leading space per SSE spec, but not more (tokens can start with a space)
        lines.push(val.startsWith(" ") ? val.slice(1) : val);
      }
    }

    const data = lines.join("\n");
    if (type === "done") return true;
    if (type === "token" && data) onToken(data);
    else if (type === "sources" && data) {
      try {
        onSources(JSON.parse(data) as SourceChunk[]);
      } catch { /* skip bad payload */ }
    }
    return false;
  }

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    // sse_starlette uses \r\n by default; normalize so \n\n splits events correctly
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");

    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";

    for (const block of blocks) {
      if (block.trim() && handleEvent(block)) {
        onDone();
        return;
      }
    }
  }

  if (buffer.trim()) handleEvent(buffer);
  onDone();
}
