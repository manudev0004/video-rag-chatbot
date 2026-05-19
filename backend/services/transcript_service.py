import os
import re
import logging

from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

from ..monitoring import track

load_dotenv()
logger = logging.getLogger(__name__)


def extract_video_id(url: str) -> str:
    """Extract and return the YouTube video ID from a URL."""
    patterns = [
        r"(?:v=)([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"shorts/([A-Za-z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract a YouTube video ID from: {url}")


@track("transcript_fetch")
def get_transcript(url: str) -> str:
    """Fetch and return the full transcript for a YouTube video as plain text."""
    video_id = extract_video_id(url)
    logger.info("Fetching transcript for video_id=%s", video_id)

    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id)
    except TranscriptsDisabled:
        raise RuntimeError(f"Transcripts are disabled for video: {video_id}")
    except NoTranscriptFound:
        raise RuntimeError(f"No transcript found for video: {video_id}")

    transcript = " ".join(snippet.text for snippet in fetched)
    logger.info("Transcript fetched: %d characters", len(transcript))
    return transcript
