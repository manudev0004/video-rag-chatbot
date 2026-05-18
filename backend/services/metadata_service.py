import os
import re
import logging

import isodate
from dotenv import load_dotenv
from googleapiclient.discovery import build

from .transcript_service import extract_video_id

load_dotenv()
logger = logging.getLogger(__name__)


def get_video_metadata(url: str) -> dict:
    """Fetch and return metadata for a YouTube video via the Data API v3."""
    video_id = extract_video_id(url)
    logger.info("Fetching metadata for video_id=%s", video_id)

    youtube = build("youtube", "v3", developerKey=os.environ["YOUTUBE_API_KEY"])

    video_resp = youtube.videos().list(
        part="snippet,statistics,contentDetails",
        id=video_id,
    ).execute()

    if not video_resp.get("items"):
        raise ValueError(f"No video found for ID: {video_id}")

    item = video_resp["items"][0]
    snippet = item["snippet"]
    stats = item.get("statistics", {})
    content = item["contentDetails"]

    views = int(stats.get("viewCount", 0))
    likes = int(stats.get("likeCount", 0))
    comments = int(stats.get("commentCount", 0))

    duration_seconds = int(
        isodate.parse_duration(content["duration"]).total_seconds()
    )

    description = snippet.get("description", "")
    hashtags = re.findall(r"#\w+", description)[:20]

    channel_resp = youtube.channels().list(
        part="statistics",
        id=snippet["channelId"],
    ).execute()

    subscriber_count = 0
    if channel_resp.get("items"):
        channel_stats = channel_resp["items"][0].get("statistics", {})
        subscriber_count = int(channel_stats.get("subscriberCount", 0))

    engagement_rate = (
        round((likes + comments) / views * 100, 4) if views > 0 else 0.0
    )

    return {
        "video_id": video_id,
        "title": snippet["title"],
        "creator": snippet["channelTitle"],
        "upload_date": snippet["publishedAt"][:10],
        "duration_seconds": duration_seconds,
        "views": views,
        "likes": likes,
        "comments": comments,
        "hashtags": hashtags,
        "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
        "engagement_rate": engagement_rate,
        "subscriber_count": subscriber_count,
    }
