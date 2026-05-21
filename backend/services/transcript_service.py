import os
import re
import glob
import logging
import tempfile

from dotenv import load_dotenv
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ProxyError as RequestsProxyError
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig, WebshareProxyConfig
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    CouldNotRetrieveTranscript,
    VideoUnavailable,
    AgeRestricted,
    IpBlocked,
    RequestBlocked,
)

from ..monitoring import track

load_dotenv()
logger = logging.getLogger(__name__)

_ENGLISH_LANGS = ["en", "en-US", "en-GB", "en-orig"]

_yt_proxy = os.getenv("YT_PROXY")


def _yt_api() -> YouTubeTranscriptApi:
    """Return a YouTubeTranscriptApi instance with proxy if YT_PROXY is set.

    Webshare URLs (p.webshare.io) are routed through WebshareProxyConfig so that
    prevent_keeping_connections_alive is set. Without it the requests Session reuses
    the same TCP connection and the rotating proxy never actually rotates IPs.
    """
    if not _yt_proxy:
        return YouTubeTranscriptApi()
    from urllib.parse import urlparse
    parsed = urlparse(_yt_proxy)
    if "webshare.io" in (parsed.hostname or ""):
        return YouTubeTranscriptApi(
            proxy_config=WebshareProxyConfig(
                proxy_username=parsed.username or "",
                proxy_password=parsed.password or "",
            )
        )
    return YouTubeTranscriptApi(proxy_config=GenericProxyConfig(http_url=_yt_proxy, https_url=_yt_proxy))

_url_patterns = [
    # YouTube - exactly 11 chars; lookahead stops it matching Facebook's longer numeric IDs
    r"(?:v=)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])",
    r"youtu\.be/([A-Za-z0-9_-]{11})",
    r"(?:shorts|embed|live|v)/([A-Za-z0-9_-]{11})",
    # Instagram
    r"instagram\.com/(?:reels?|p|tv)/([A-Za-z0-9_-]+)",
    r"instagram\.com/[^/?#]+/reel/([A-Za-z0-9_-]+)",
    r"instagram\.com/stories/[^/?#]+/(\d+)",
    # Facebook - direct video/reel
    r"facebook\.com/reel/(\d+)",
    r"facebook\.com/[^/?#]+/videos/(?:[^/?#]+/)?(\d+)",
    r"facebook\.com/(?:video\.php|watch).*?[?&]v=(\d+)",
    # Facebook - posts and permalinks
    r"facebook\.com/groups/[^/?#]+/(?:posts|permalink)/([A-Za-z0-9]+)",
    r"facebook\.com/[^/?#]+/posts/([A-Za-z0-9]+)",
    r"facebook\.com/permalink\.php.*?story_fbid=([A-Za-z0-9]+)",
    r"facebook\.com/story\.php.*?story_fbid=([A-Za-z0-9]+)",
    # Facebook - share short links
    r"facebook\.com/share/[rv]/([A-Za-z0-9_-]+)",
    # fb.watch
    r"fb\.watch/([A-Za-z0-9_-]+)",
]

_youtube_re = re.compile(r"youtube\.com|youtu\.be")
_QUIET_OPTS: dict = {"skip_download": True, "quiet": True, "no_warnings": True}


def extract_video_id(url: str) -> str:
    """Extract and return the video ID from a URL. Supports YouTube, Instagram, and Facebook."""
    for pattern in _url_patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract a video ID from: {url}")


def is_youtube_url(url: str) -> bool:
    """Return True if the URL points to a YouTube video."""
    return bool(_youtube_re.search(url))


def fetch_ydlp_info(url: str) -> dict:
    """Run yt-dlp extract_info once and return the info dict. Raises RuntimeError on failure."""
    import yt_dlp
    from yt_dlp.utils import DownloadError
    with yt_dlp.YoutubeDL(_QUIET_OPTS) as ydl:
        try:
            return ydl.extract_info(url, download=False)
        except DownloadError as exc:
            raise RuntimeError(f"yt-dlp failed to extract info for {url}: {exc}")


def _parse_vtt(path: str) -> str:
    """Parse a WebVTT file and return plain text with duplicate lines removed."""
    lines = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if (
                not line
                or line.startswith("WEBVTT")
                or line.startswith("NOTE")
                or re.match(r"^\d{2}:\d{2}:\d{2}[.,]\d{3} -->", line)
                or re.match(r"^\d+$", line)
            ):
                continue
            cleaned = re.sub(r"<[^>]+>", "", line).strip()
            if cleaned:
                lines.append(cleaned)

    # Auto-captions repeat lines; deduplicate consecutive dupes
    deduped: list[str] = []
    for line in lines:
        if not deduped or line != deduped[-1]:
            deduped.append(line)

    return " ".join(deduped)


def _fallback_from_metadata(info: dict, url: str) -> str:
    """Build a best-effort text from video metadata when no subtitles exist."""
    parts = []
    if info.get("title"):
        parts.append(f"Title: {info['title']}")
    if info.get("description"):
        parts.append(f"Description: {info['description']}")
    tags = info.get("tags") or []
    if tags:
        parts.append(f"Tags: {', '.join(tags[:20])}")
    if not parts:
        raise RuntimeError(f"No subtitles or description found for: {url}")
    text = "[no transcript] " + " | ".join(parts)
    logger.info("No subtitles found for %s, using metadata (%d chars)", url, len(text))
    return text


def _fetch_via_ytdlp(url: str, info: dict | None = None) -> str:
    """Fetch subtitles via yt-dlp. Supports YouTube, YouTube Shorts, Instagram, Facebook."""
    import yt_dlp
    from yt_dlp.utils import DownloadError

    # Step 1: find out what subtitle languages are actually available
    if info is None:
        with yt_dlp.YoutubeDL(_QUIET_OPTS) as ydl:
            info = ydl.extract_info(url, download=False)

    manual_subs: dict = info.get("subtitles") or {}
    auto_subs: dict = info.get("automatic_captions") or {}

    # Prefer English; otherwise take the first available language
    chosen = next((lang for lang in _ENGLISH_LANGS if lang in manual_subs or lang in auto_subs), None)
    if chosen is None:
        chosen = next(iter(manual_subs), next(iter(auto_subs), None))
    if chosen is None:
        return _fallback_from_metadata(info, url)

    logger.info("Downloading subtitles in lang=%s via yt-dlp", chosen)

    # Step 2: download only that language; no translation fallback, no 429
    with tempfile.TemporaryDirectory() as tmpdir:
        ydl_opts = {
            **_QUIET_OPTS,
            "writesubtitles": chosen in manual_subs,
            "writeautomaticsub": chosen in auto_subs,
            "subtitleslangs": [chosen],
            "outtmpl": os.path.join(tmpdir, "%(id)s"),
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except DownloadError as exc:
            msg = str(exc)
            if "429" in msg or "Too Many Requests" in msg:
                raise RuntimeError(
                    "YouTube is rate-limiting requests from this IP. Wait a minute and try again."
                )
            raise RuntimeError(f"yt-dlp subtitle download failed: {exc}")

        sub_files = glob.glob(os.path.join(tmpdir, "*.vtt")) + glob.glob(os.path.join(tmpdir, "*.srt"))
        if not sub_files:
            raise RuntimeError(f"No subtitle file written for: {url}")

        transcript = _parse_vtt(sub_files[0])

    logger.info("Transcript fetched via yt-dlp (lang=%s): %d chars", chosen, len(transcript))
    return transcript


def _fetch_any_transcript(api: YouTubeTranscriptApi, video_id: str) -> str:
    """Try to fetch any available transcript when the preferred language isn't found."""
    tlist = api.list(video_id)
    transcript = next(iter(tlist), None)
    if transcript is None:
        raise RuntimeError(f"No transcripts available for video {video_id}.")
    fetched = transcript.fetch()
    parts = [snippet.text.replace("\n", " ").strip() for snippet in fetched]
    result = " ".join(p for p in parts if p)
    logger.info(
        "Transcript fetched (lang=%s, generated=%s): %d chars",
        transcript.language_code, transcript.is_generated, len(result),
    )
    return result


@track("transcript_fetch")
def get_transcript(url: str, info: dict | None = None) -> str:
    """Fetch the full transcript for a video URL as plain text.

    For YouTube: tries youtube-transcript-api (English first, then any language),
    falls back to yt-dlp on persistent failure.
    For Instagram and Facebook: uses yt-dlp directly.
    Pass info to skip the yt-dlp extract_info network call when already fetched.
    """
    if is_youtube_url(url):
        video_id = extract_video_id(url)
        logger.info("Fetching transcript for video_id=%s", video_id)
        api = _yt_api()
        try:
            fetched = api.fetch(video_id)
            parts = [snippet.text.replace("\n", " ").strip() for snippet in fetched]
            transcript = " ".join(p for p in parts if p)
            logger.info("Transcript fetched via API: %d chars", len(transcript))
            return transcript
        except VideoUnavailable:
            raise RuntimeError(f"Video {video_id} is unavailable (private or deleted).")
        except AgeRestricted:
            raise RuntimeError(f"Video {video_id} is age-restricted and cannot be accessed without login.")
        except IpBlocked:
            raise RuntimeError("YouTube has blocked this IP temporarily. Try again later.")
        except RequestBlocked:
            raise RuntimeError("YouTube blocked this request. Try again later or configure a proxy via YT_PROXY.")
        except NoTranscriptFound:
            # English not available; try any language the video has
            logger.info("No English transcript for %s, trying any available language", video_id)
            try:
                return _fetch_any_transcript(api, video_id)
            except RuntimeError:
                raise
            except Exception as exc:
                logger.warning("Any-language fetch failed (%s), falling back to yt-dlp", exc)
        except (TranscriptsDisabled, CouldNotRetrieveTranscript) as exc:
            logger.warning("youtube-transcript-api failed (%s), falling back to yt-dlp", exc)

    logger.info("Fetching transcript via yt-dlp: %s", url)
    try:
        return _fetch_via_ytdlp(url, info)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"No transcript available for {url}: {exc}")
