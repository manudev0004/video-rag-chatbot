import type { IngestResponse, VideoMetadata } from "@/types";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`Request failed (${res.status}): ${text}`);
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

export async function streamChat(
  question: string,
  sessionId: string,
  onToken: (token: string) => void,
  onSources: (sources: Array<Record<string, unknown>>) => void,
  onDone: () => void
): Promise<void> {
  const res = await fetch(`${BASE_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, session_id: sessionId }),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`Chat request failed (${res.status}): ${text}`);
  }

  if (!res.body) {
    throw new Error("Response body is null — streaming not supported by this environment");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    const chunk = decoder.decode(value, { stream: true });

    for (const line of chunk.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;

      const raw = trimmed.slice(5).trim();
      if (raw === "[DONE]") {
        onDone();
        return;
      }

      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        // malformed SSE line — skip it
        continue;
      }

      if (typeof parsed.token === "string") {
        onToken(parsed.token);
      } else if (Array.isArray(parsed.sources)) {
        onSources(parsed.sources as Array<Record<string, unknown>>);
      }
    }
  }

  onDone();
}
