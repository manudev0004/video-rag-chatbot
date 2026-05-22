import os
import re
import glob
import logging
import tempfile

from dotenv import load_dotenv
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ProxyError as RequestsProxyError
from requests.exceptions import RetryError as RequestsRetryError
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
    """Return a YouTubeTranscriptApi instance, with proxy if YT_PROXY is set."""
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
    # youtube IDs are exactly 11 chars; lookahead prevents matching facebook's longer numeric IDs
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
_QUIET_OPTS: dict = {
    "skip_download": True,
    "quiet": True,
    "no_warnings": True,
    "extractor_args": {"youtube": {"player_client": ["tv_embedded", "ios"]}},
    "socket_timeout": 30,
    "http_chunk_size": 1048576,  # 1MB chunks instead of loading full response
}
if _yt_proxy:
    _QUIET_OPTS["proxy"] = _yt_proxy

_cookie_file: str | None = None

_cookie_parts = [
    c for c in [
        os.getenv("INSTAGRAM_COOKIES"),
        os.getenv("YOUTUBE_COOKIES"),
    ] if c
]
if _cookie_parts:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as _f:
        _f.write("\n".join(_cookie_parts))
        _cookie_file = _f.name
    logger.info("Cookies loaded from env (%d source(s))", len(_cookie_parts))


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


def _ydlp_opts() -> dict:
    """Base yt-dlp options, with cookie file injected if available."""
    opts = dict(_QUIET_OPTS)
    if _cookie_file:
        opts["cookiefile"] = _cookie_file
    return opts


def fetch_ydlp_info(url: str) -> dict:
    """Pull video info with yt-dlp, no download."""
    import yt_dlp
    from yt_dlp.utils import DownloadError
    with yt_dlp.YoutubeDL(_ydlp_opts()) as ydl:
        try:
            return ydl.extract_info(url, download=False)
        except DownloadError as exc:
            raise RuntimeError(f"yt-dlp failed to extract info for {url}: {exc}")


_IG_APP_ID = "936619743392459"
_IG_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
_ig_media_cache: dict[str, dict] = {}


def _ig_oembed_author(url: str) -> str | None:
    """Get the author username via Instagram's public oEmbed endpoint."""
    import urllib.parse as _urlparse
    import requests as _req

    encoded = _urlparse.quote(url, safe="")
    try:
        resp = _req.get(
            f"https://www.instagram.com/api/v1/oembed/?url={encoded}",
            headers={"User-Agent": _IG_UA},
            timeout=15,
        )
        logger.info("Instagram oEmbed status=%s for %s", resp.status_code, url)
        if resp.status_code == 200:
            return (resp.json() or {}).get("author_name")
    except (_req.RequestException, ValueError) as exc:
        logger.info("Instagram oEmbed failed for %s: %s", url, exc)
    return None


def _ig_profile_followers(username: str) -> int | None:
    """Get follower count by username - tries the profile API, falls back to the profile page."""
    import requests as _req

    try:
        resp = _req.get(
            f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}",
            headers={
                "User-Agent": _IG_UA,
                "X-IG-App-ID": _IG_APP_ID,
                "Accept": "*/*",
            },
            timeout=15,
        )
        logger.info("Instagram profile API status=%s for %s", resp.status_code, username)
        if resp.status_code == 200:
            count = (
                (resp.json() or {})
                .get("data", {})
                .get("user", {})
                .get("edge_followed_by", {})
                .get("count")
            )
            if count is not None:
                return int(count)
    except (_req.RequestException, ValueError) as exc:
        logger.info("Instagram profile API failed for %s: %s", username, exc)

    try:
        resp = _req.get(
            f"https://www.instagram.com/{username}/",
            headers={"User-Agent": _IG_UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=15,
        )
        logger.info("Instagram profile page status=%s for %s", resp.status_code, username)
        if resp.status_code != 200:
            return None
        desc_m = re.search(
            r'<meta property="og:description" content="([^"]+)"', resp.text
        )
        if not desc_m:
            return None
        # e.g. "3M Followers, 166 Following, 422 Posts - See Instagram..."
        num_m = re.search(r"([\d.,]+)\s*([KMBkmb])?\s*Followers", desc_m.group(1))
        if not num_m:
            return None
        raw = num_m.group(1).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            return None
        suffix = (num_m.group(2) or "").lower()
        multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix, 1)
        count = int(value * multiplier)
        logger.info("Instagram followers (og:description) for %s: %s", username, count)
        return count
    except (_req.RequestException, ValueError) as exc:
        logger.info("Instagram profile page failed for %s: %s", username, exc)
    return None


def _fetch_instagram_media(url: str) -> dict:
    """Get views, follower count, and username for an Instagram reel.

    Uses the public embed page. For gated reels, falls back to oEmbed
    for the username then the profile API for the follower count.
    """
    import requests as _req

    if url in _ig_media_cache:
        return _ig_media_cache[url]

    result: dict = {"views": None, "follower_count": None, "username": None}
    shortcode_m = re.search(r"/(?:p|reel|reels|tv)/([^/?#]+)", url)
    if not shortcode_m:
        _ig_media_cache[url] = result
        return result
    shortcode = shortcode_m.group(1)

    embed_url = f"https://www.instagram.com/reel/{shortcode}/embed/captioned/"
    try:
        resp = _req.get(
            embed_url,
            headers={"User-Agent": _IG_UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=15,
        )
        logger.info("Instagram embed status=%s for %s", resp.status_code, shortcode)
        if resp.status_code == 200:
            html = resp.text
            views_m = (
                re.search(r'\\"video_view_count\\":(\d+)', html)
                or re.search(r'\\"play_count\\":(\d+)', html)
            )
            if views_m:
                result["views"] = int(views_m.group(1))
            followers_m = re.search(r'\\"edge_followed_by\\":\{\\"count\\":(\d+)\}', html)
            if followers_m:
                result["follower_count"] = int(followers_m.group(1))
            user_m = re.search(r'\\"username\\":\\"([^\\"]+)\\"', html)
            if user_m:
                result["username"] = user_m.group(1)
    except _req.RequestException as exc:
        logger.warning("Instagram embed fetch failed for %s: %s", shortcode, exc)

    if not result["username"]:
        result["username"] = _ig_oembed_author(url)
    if result["username"] and result["follower_count"] is None:
        result["follower_count"] = _ig_profile_followers(result["username"])

    logger.info(
        "Instagram media data for %s: views=%s followers=%s username=%s",
        shortcode, result["views"], result["follower_count"], result["username"],
    )
    _ig_media_cache[url] = result
    return result


def fetch_facebook_follower_count(uploader_id: str | int | None) -> int | None:
    """Read follower count from a Facebook Page's meta description.

    The page description looks like "PageName. 24,151 likes · 4,525 talking about
    this" in English or "Name. 1,40,654 likes ." in Indian locale (lakh format).
    """
    if not uploader_id:
        return None
    uid = str(uploader_id)
    if not uid.isdigit():
        return None
    import html as _html
    import requests as _req

    try:
        resp = _req.get(
            f"https://www.facebook.com/{uid}/",
            headers={"User-Agent": _IG_UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=15,
            allow_redirects=True,
        )
        logger.info("Facebook page status=%s for id=%s", resp.status_code, uid)
        if resp.status_code != 200:
            return None
        desc_m = re.search(r'<meta name="description"[^>]+content="([^"]+)"', resp.text)
        if not desc_m:
            logger.info("No description meta tag on Facebook page %s", uid)
            return None
        desc = _html.unescape(desc_m.group(1))
        # skip the page name by splitting on ". ", then take the first number
        parts = desc.split(". ", 1)
        tail = parts[1] if len(parts) == 2 else desc
        num_m = re.search(r"([\d][\d,.\s]*)", tail)
        if not num_m:
            return None
        raw = re.sub(r"[,.\s]", "", num_m.group(1))
        if not raw.isdigit():
            return None
        count = int(raw)
        logger.info("Facebook follower count for id=%s: %s", uid, count)
        return count
    except (_req.RequestException, ValueError) as exc:
        logger.warning("Facebook page lookup failed for %s: %s", uid, exc)
    return None


def fetch_instagram_follower_count(
    url: str | None = None,
    uploader_id: str | None = None,
    username: str | None = None,
) -> int | None:
    """Get Instagram follower count for a reel URL."""
    if url:
        cached = _fetch_instagram_media(url)
        if cached.get("follower_count") is not None:
            return int(cached["follower_count"])
        if cached.get("username") and not username:
            username = cached["username"]
    if username:
        return _ig_profile_followers(username)
    return None


def scrape_instagram_views(url: str) -> int | None:
    """Get Instagram reel view count from the embed page cache."""
    cached = _fetch_instagram_media(url)
    return int(cached["views"]) if cached.get("views") is not None else None


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

    # auto captions repeat a lot, strip consecutive dupes
    deduped: list[str] = []
    for line in lines:
        if not deduped or line != deduped[-1]:
            deduped.append(line)

    return " ".join(deduped)


def _get_media_url(info: dict) -> str | None:
    """Return a direct playable URL from yt-dlp info, for sending to AssemblyAI."""
    if info.get("url"):
        return info["url"]
    formats = info.get("formats") or []
    # prefer a format that has audio; work backwards from best quality
    for fmt in reversed(formats):
        if fmt.get("url") and fmt.get("acodec") != "none":
            return fmt["url"]
    # fallback: any format with a URL
    for fmt in reversed(formats):
        if fmt.get("url"):
            return fmt["url"]
    return None


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
    """Get subtitles via yt-dlp. Used for Instagram and Facebook."""
    import yt_dlp
    from yt_dlp.utils import DownloadError

    if info is None:
        with yt_dlp.YoutubeDL(_ydlp_opts()) as ydl:
            info = ydl.extract_info(url, download=False)

    manual_subs: dict = info.get("subtitles") or {}
    auto_subs: dict = info.get("automatic_captions") or {}

    # prefer english, fall back to whatever is available
    chosen = next((lang for lang in _ENGLISH_LANGS if lang in manual_subs or lang in auto_subs), None)
    if chosen is None:
        chosen = next(iter(manual_subs), next(iter(auto_subs), None))
    if chosen is None:
        # no captions - try to transcribe the audio via AssemblyAI
        media_url = _get_media_url(info)
        if media_url:
            logger.info("No subtitles found, sending audio to AssemblyAI for %s", url)
            return _fetch_via_assemblyai(media_url)
        return _fallback_from_metadata(info, url)

    logger.info("Downloading subtitles in lang=%s via yt-dlp", chosen)

    with tempfile.TemporaryDirectory() as tmpdir:
        ydl_opts = {
            **_ydlp_opts(),
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


def _fetch_via_assemblyai(url: str) -> str:
    """Transcribe via AssemblyAI. Passes the URL directly; AssemblyAI handles the download."""
    import assemblyai as aai

    api_key = os.getenv("ASSEMBLYAI_API_KEY")
    if not api_key:
        raise RuntimeError("ASSEMBLYAI_API_KEY is not set in .env")

    aai.settings.api_key = api_key
    config = aai.TranscriptionConfig(speech_models=["universal-3-pro"])
    transcriber = aai.Transcriber(config=config)

    logger.info("Submitting to AssemblyAI: %s", url)
    transcript = transcriber.transcribe(url)

    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"AssemblyAI transcription failed: {transcript.error}")

    text = transcript.text or ""
    logger.info("AssemblyAI transcript received: %d chars", len(text))
    return text


def _youtube_audio_url(url: str) -> str:
    """Get a direct YouTube audio stream URL via pytubefix."""
    from pytubefix import YouTube

    yt = YouTube(url)
    stream = yt.streams.filter(only_audio=True).order_by("abr").desc().first()
    if not stream or not stream.url:
        raise RuntimeError(f"No audio stream available for {url}")
    return stream.url


def _resolve_youtube_audio(url: str, video_id: str) -> str:
    """Resolve a direct YouTube audio CDN URL for AssemblyAI.

    AssemblyAI can't fetch youtube.com watch pages (returns HTML), so we
    extract the audio CDN URL first. Tries yt-dlp (tv_embedded/ios clients
    bypass most bot detection), then pytubefix as a backup.
    """
    errors: list[str] = []
    try:
        info = fetch_ydlp_info(url)
        media_url = _get_media_url(info)
        if media_url:
            logger.info("YouTube audio URL resolved via yt-dlp for %s", video_id)
            return media_url
        errors.append("yt-dlp: no playable format in formats list")
    except Exception as exc:
        errors.append(f"yt-dlp: {exc}")

    try:
        audio_url = _youtube_audio_url(url)
        logger.info("YouTube audio URL resolved via pytubefix for %s", video_id)
        return audio_url
    except Exception as exc:
        errors.append(f"pytubefix: {exc}")

    raise RuntimeError(f"YouTube audio extraction failed: {' | '.join(errors)}")


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
    """Get transcript text for a video URL.

    YouTube: tries youtube-transcript-api first, falls back to AssemblyAI if blocked.
    Instagram/Facebook: uses yt-dlp. Pass info to reuse an already-fetched yt-dlp result.
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
        except (IpBlocked, RequestBlocked) as exc:
            logger.warning("Transcript API blocked for %s (%s), falling back to AssemblyAI", video_id, exc)
        except (RequestsProxyError, RequestsConnectionError, RequestsRetryError) as exc:
            logger.warning("Proxy/connection error for %s (%s), falling back to AssemblyAI", video_id, exc)
        except NoTranscriptFound:
            logger.info("No English transcript for %s, trying any available language", video_id)
            try:
                return _fetch_any_transcript(api, video_id)
            except RuntimeError:
                raise
            except Exception as exc:
                logger.warning("Any-language fetch failed (%s), falling back to AssemblyAI", exc)
        except (TranscriptsDisabled, CouldNotRetrieveTranscript) as exc:
            logger.warning("youtube-transcript-api failed (%s), falling back to AssemblyAI", exc)

        # transcript API blocked or subtitles disabled — try to extract audio for AssemblyAI
        try:
            audio_url = _resolve_youtube_audio(url, video_id)
            return _fetch_via_assemblyai(audio_url)
        except Exception as exc:
            logger.warning(
                "YouTube audio path failed for %s (%s), falling back to metadata",
                video_id, exc,
            )

        # last resort: build a [no transcript] placeholder from yt-dlp metadata so the video
        # still gets indexed and the LLM is told its content is limited to title/description
        try:
            info = fetch_ydlp_info(url)
            return _fallback_from_metadata(info, url)
        except Exception as exc:
            logger.warning("yt-dlp info failed for %s (%s), using minimal placeholder", video_id, exc)

        return (
            f"[no transcript] YouTube video {video_id}. No captions were available and "
            "the audio could not be retrieved from this environment."
        )

    logger.info("Fetching transcript via yt-dlp: %s", url)
    try:
        return _fetch_via_ytdlp(url, info)
    except Exception as exc:
        logger.warning("yt-dlp path failed for %s (%s), falling back to metadata", url, exc)

    try:
        if info is None:
            info = fetch_ydlp_info(url)
        return _fallback_from_metadata(info, url)
    except Exception as exc:
        logger.warning("Metadata fallback failed for %s (%s), using minimal placeholder", url, exc)

    return f"[no transcript] Video at {url}. No captions and audio could not be retrieved."
