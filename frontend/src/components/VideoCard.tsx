import Image from "next/image";
import type { VideoMetadata } from "@/types";

interface VideoCardProps {
  label: string;
  data: VideoMetadata | null;
  loading: boolean;
  indexing?: boolean;
  onDelete?: () => void;
}

function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function formatCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function engagementBadge(rate: number): string {
  if (rate >= 8) return "bg-green-100 text-green-700";
  if (rate >= 4) return "bg-amber-100 text-amber-700";
  return "bg-red-100 text-red-700";
}

function Skeleton({ className }: { className: string }) {
  return <div className={`animate-pulse rounded bg-zinc-200 ${className}`} />;
}

const STATS = ["Views", "Likes", "Comments"] as const;

export default function VideoCard({ label, data, loading, indexing, onDelete }: VideoCardProps) {
  return (
    <div className="relative flex h-full flex-col gap-3 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm">
      {onDelete && (
        <button
          type="button"
          onClick={onDelete}
          aria-label="Remove video"
          className="absolute right-2 top-2 rounded p-0.5 text-zinc-300 hover:bg-zinc-100 hover:text-zinc-600"
        >
          <svg className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor">
            <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
          </svg>
        </button>
      )}

      <p className="shrink-0 text-xs font-semibold uppercase tracking-wide text-zinc-400">
        {label}
      </p>

      {loading && (
        <>
          <Skeleton className="aspect-video w-full shrink-0" />
          <div className="space-y-2">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
          </div>
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-2/3" />
        </>
      )}

      {!loading && !data && (
        <div className="flex aspect-video items-center justify-center rounded-lg bg-zinc-100 text-sm text-zinc-400">
          No video loaded
        </div>
      )}

      {!loading && data && (
        <>
          <div className="relative aspect-video w-full shrink-0 overflow-hidden rounded-lg bg-zinc-100">
            <Image
              src={data.thumbnail_url}
              alt={data.title}
              fill
              sizes="(max-width: 768px) 100vw, 50vw"
              className="object-cover"
            />
            <span className="absolute bottom-2 right-2 rounded bg-black/70 px-1.5 py-0.5 text-xs font-medium text-white">
              {formatDuration(data.duration_seconds)}
            </span>
          </div>

          {/* Fixed the height so it donot changes regardless of title length */}
          <div className="shrink-0">
            <h3 className="line-clamp-2 h-9 text-sm font-semibold leading-snug text-zinc-900">
              {data.title}
            </h3>
            <p className="mt-1 truncate text-xs text-zinc-500">
              {data.creator}
              <span className="mx-1 text-zinc-300">·</span>
              {formatCount(data.subscriber_count)} subscribers
            </p>
          </div>

          <div className="grid shrink-0 grid-cols-3 gap-2 text-center">
            {([data.views, data.likes, data.comments] as const).map((value, i) => (
              <div key={STATS[i]} className="rounded-lg bg-zinc-50 px-2 py-2">
                <p className="text-xs text-zinc-400">{STATS[i]}</p>
                <p className="text-sm font-semibold text-zinc-800">{formatCount(value)}</p>
              </div>
            ))}
          </div>

          <div className="flex shrink-0 items-center justify-between text-xs">
            <span className="text-zinc-500">Engagement rate</span>
            <span className={`rounded-full px-2 py-0.5 font-semibold ${engagementBadge(data.engagement_rate)}`}>
              {data.engagement_rate.toFixed(2)}%
            </span>
          </div>

          {data.hashtags.length > 0 && (
            <div className="flex shrink-0 flex-wrap gap-1">
              {data.hashtags.slice(0, 5).map((tag) => (
                <span
                  key={tag}
                  className="rounded-full bg-blue-50 px-2 py-0.5 text-xs text-blue-600"
                >
                  {tag.startsWith("#") ? tag : `#${tag}`}
                </span>
              ))}
            </div>
          )}

          {indexing && (
            <div className="flex shrink-0 items-center gap-1.5 text-xs text-amber-600">
              <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400" />
              Indexing...
            </div>
          )}
        </>
      )}
    </div>
  );
}
