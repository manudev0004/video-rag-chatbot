export interface VideoMetadata {
  video_id: string;
  title: string;
  creator: string;
  upload_date: string;
  duration_seconds: number;
  views: number;
  likes: number;
  comments: number;
  hashtags: string[];
  thumbnail_url: string;
  engagement_rate: number;
  subscriber_count: number;
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
}
