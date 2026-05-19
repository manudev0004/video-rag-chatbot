from pydantic import BaseModel, field_validator


class IngestRequest(BaseModel):
    urls: list[str]

    @field_validator("urls")
    @classmethod
    def requires_at_least_two(cls, values: list[str]) -> list[str]:
        if len(values) < 2:
            raise ValueError("At least 2 video URLs are required.")
        return values


class ChatRequest(BaseModel):
    question: str
    session_id: str = "default"


class VideoMetadata(BaseModel):
    video_id: str
    title: str
    creator: str
    upload_date: str
    duration_seconds: int
    views: int
    likes: int
    comments: int
    hashtags: list[str]
    thumbnail_url: str
    engagement_rate: float
    subscriber_count: int


class IngestResponse(BaseModel):
    status: str
    videos: dict[str, VideoMetadata]


class ChatResponse(BaseModel):
    answer: str
    sources: list[dict]
