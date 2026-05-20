import os
import re
import logging

import isodate
from dotenv import load_dotenv
from googleapiclient.discovery import build

from .transcript_service import extract_video_id, is_youtube_url

load_dotenv()
logger = logging.getLogger(__name__)


def _metadata_via_ytdlp(url: str) -> dict:
    """Extract video metadata for non-YouTube URLs using yt-dlp."""
    import yt_dlp

    ydl_opts = {"skip_download": True, "quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    views = int(info.get("view_count") or 0)
    likes = int(info.get("like_count") or 0)
    comments = int(info.get("comment_count") or 0)
    engagement_rate = round((likes + comments) / views * 100, 4) if views > 0 else 0.0

    raw_date = info.get("upload_date", "")
    upload_date = (
        f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        if len(raw_date) == 8
        else raw_date
    )

    tags = info.get("tags") or []
    hashtags = [f"#{t}" for t in tags[:20]]

    return {
        "video_id": info.get("id", ""),
        "title": info.get("title", ""),
        "creator": info.get("uploader") or info.get("channel", ""),
        "upload_date": upload_date,
        "duration_seconds": int(info.get("duration") or 0),
        "views": views,
        "likes": likes,
        "comments": comments,
        "hashtags": hashtags,
        "thumbnail_url": info.get("thumbnail", ""),
        "engagement_rate": engagement_rate,
        "subscriber_count": int(info.get("channel_follower_count") or 0),
    }


def get_video_metadata(url: str) -> dict:
    """Fetch and return metadata for a video URL.

    For YouTube URLs uses the YouTube Data API v3, yt-dlp for everything else.
    """
    if not is_youtube_url(url):
        logger.info("Fetching metadata via yt-dlp for: %s", url)
        return _metadata_via_ytdlp(url)

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
