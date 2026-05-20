import os
import re
import ssl
import logging

import isodate
from dotenv import load_dotenv
from googleapiclient.discovery import build

from .transcript_service import extract_video_id, is_youtube_url

load_dotenv()
logger = logging.getLogger(__name__)

_reactions_re = re.compile(r"([\d,.]+[KkMm]?)\s+reactions?", re.IGNORECASE)

_yt_service = None


def _get_yt_service():
    """Return the shared YouTube API client, building it on first call."""
    global _yt_service
    if _yt_service is None:
        _yt_service = build("youtube", "v3", developerKey=os.environ["YOUTUBE_API_KEY"])
    return _yt_service


def reset_yt_service() -> None:
    """Drop the cached client so the next call builds a fresh one."""
    global _yt_service
    _yt_service = None


def _metadata_via_ytdlp(url: str, info: dict | None = None) -> dict:
    """Extract video metadata for non-YouTube URLs using yt-dlp."""
    import yt_dlp

    if info is None:
        ydl_opts = {"skip_download": True, "quiet": True, "no_warnings": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

    raw_views = info.get("view_count")
    views = int(raw_views) if raw_views is not None else None
    likes = int(info.get("like_count")) if info.get("like_count") is not None else None
    comments = int(info.get("comment_count")) if info.get("comment_count") is not None else None

    # Facebook doesn't expose likes via the API; parse reactions count from the title
    # e.g. "119K views · 464 reactions | Why was Queen Maeve..."
    if likes is None:
        title_str = info.get("title", "")
        m = _reactions_re.search(title_str)
        if m:
            raw = m.group(1).replace(",", "")
            factor = {"k": 1_000, "m": 1_000_000}
            suffix = raw[-1].lower()
            if suffix in factor:
                likes = int(float(raw[:-1]) * factor[suffix])
            else:
                likes = int(float(raw))
    known_likes = likes or 0
    known_comments = comments or 0
    engagement_rate = round((known_likes + known_comments) / views * 100, 4) if views else 0.0

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
        "subscriber_count": int(info.get("channel_follower_count")) if info.get("channel_follower_count") is not None else None,
    }


def get_video_metadata(url: str, info: dict | None = None) -> dict:
    """Fetch and return metadata for a video URL.

    For YouTube URLs uses the YouTube Data API v3, yt-dlp for everything else.
    Pass info to skip the yt-dlp extract_info network call when already fetched.
    """
    if not is_youtube_url(url):
        logger.info("Fetching metadata via yt-dlp for: %s", url)
        return _metadata_via_ytdlp(url, info)

    video_id = extract_video_id(url)
    logger.info("Fetching metadata for video_id=%s", video_id)

    youtube = _get_yt_service()

    try:
        video_resp = youtube.videos().list(
            part="snippet,statistics,contentDetails",
            id=video_id,
        ).execute()
    except ssl.SSLError:
        # Stale connection from the pool; reset and retry once.
        logger.warning("SSL error fetching metadata for %s, retrying with fresh client", video_id)
        reset_yt_service()
        youtube = _get_yt_service()
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
