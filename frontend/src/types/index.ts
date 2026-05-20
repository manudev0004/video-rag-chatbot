export interface VideoMetadata {
  video_id: string;
  title: string;
  creator: string;
  upload_date: string;
  duration_seconds: number;
  views: number | null;
  likes: number | null;
  comments: number | null;
  hashtags: string[];
  thumbnail_url: string;
  engagement_rate: number;
  subscriber_count: number | null;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  sources?: Array<Record<string, unknown>>;
  isStreaming?: boolean;
}

export interface IngestResponse {
  status: string;
  videos: Record<string, VideoMetadata>;
  errors: Record<string, string>;
}

export interface ChatSession {
  id: string;
  title: string;
  messages: ChatMessage[];
  videoIds: string[];
  videoData: Record<string, VideoMetadata>;
  createdAt: number;
}
