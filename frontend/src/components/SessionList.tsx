import type { ChatSession } from "@/types";

interface SessionListProps {
  sessions: ChatSession[];
  activeId: string;
  onResume: (session: ChatSession) => void;
  onDelete: (id: string) => void;
}

function timeAgo(ts: number): string {
  const diff = Date.now() - ts;
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export default function SessionList({ sessions, activeId, onResume, onDelete }: SessionListProps) {
  if (sessions.length === 0) return null;

  return (
    <div className="shrink-0">
      <p className="mb-1.5 px-0.5 text-xs text-zinc-400">Past chats</p>
      <div className="space-y-0.5">
        {sessions.map((s) => (
          <div
            key={s.id}
            className={`group flex items-center gap-2 rounded-lg px-2 py-1.5 ${
              s.id === activeId ? "bg-blue-50" : "hover:bg-zinc-50"
            }`}
          >
            <button
              type="button"
              onClick={() => onResume(s)}
              className="min-w-0 flex-1 text-left"
            >
              <span className={`block truncate text-sm ${s.id === activeId ? "text-blue-700" : "text-zinc-700"}`}>
                {s.title}
              </span>
              <span className="text-xs text-zinc-400">{timeAgo(s.createdAt)}</span>
            </button>
            <button
              type="button"
              onClick={() => onDelete(s.id)}
              aria-label="Delete chat"
              className="shrink-0 rounded p-0.5 text-zinc-300 opacity-0 transition-opacity group-hover:opacity-100 hover:text-red-500"
            >
              <svg className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor">
                <path fillRule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clipRule="evenodd" />
              </svg>
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
