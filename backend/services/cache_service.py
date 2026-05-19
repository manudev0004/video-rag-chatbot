import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_DIR = Path(os.getenv("CACHE_PATH", "./backend/video_cache"))
MAX_CACHED = 20

_lock = threading.Lock()

# in-memory index loaded from index.json at startup
# format: {video_id: {title, cached_at, embedded}}
_index: dict[str, dict] = {}


def _index_path() -> Path:
    return CACHE_DIR / "index.json"


def _video_path(video_id: str) -> Path:
    return CACHE_DIR / f"{video_id}.json"


def _load_index() -> None:
    """Read index.json into _index. Called once at module import."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _index_path()
    if path.exists():
        try:
            data = json.loads(path.read_text())
            _index.update(data)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load cache index: %s", exc)


def _save_index() -> None:
    """Write _index to index.json. Must be called with _lock held."""
    _index_path().write_text(json.dumps(_index, indent=2))


def _evict_oldest() -> None:
    """Remove the oldest entries until we are at or below MAX_CACHED. Must be called with _lock held."""
    if len(_index) <= MAX_CACHED:
        return
    by_age = sorted(_index.items(), key=lambda kv: kv[1].get("cached_at", 0))
    to_remove = by_age[: len(_index) - MAX_CACHED]
    for video_id, _ in to_remove:
        path = _video_path(video_id)
        if path.exists():
            path.unlink()
        del _index[video_id]
        logger.info("Cache evicted video_id=%s", video_id)


def save(video_id: str, metadata: dict, transcript: str) -> None:
    """Write metadata and transcript to disk and update the index."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"metadata": metadata, "transcript": transcript}
    _video_path(video_id).write_text(json.dumps(payload))
    with _lock:
        _index[video_id] = {
            "title": metadata.get("title", ""),
            "cached_at": int(time.time()),
            "embedded": False,
        }
        _evict_oldest()
        _save_index()
    logger.info("Cached video_id=%s", video_id)


def load(video_id: str) -> dict | None:
    """Return {metadata, transcript} for a video, or None if not cached."""
    path = _video_path(video_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read cache for %s: %s", video_id, exc)
        return None


def has_metadata(video_id: str) -> bool:
    """True if this video is in the index (metadata + transcript saved)."""
    return video_id in _index


def is_embedded(video_id: str) -> bool:
    """True if this video's chunks are already in ChromaDB."""
    return _index.get(video_id, {}).get("embedded", False)


def mark_embedded(video_id: str) -> None:
    """Record that this video's chunks have been stored in ChromaDB and drop the transcript from disk."""
    with _lock:
        if video_id in _index:
            _index[video_id]["embedded"] = True
            _save_index()
    path = _video_path(video_id)
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if "transcript" in data:
                path.write_text(json.dumps({"metadata": data["metadata"]}))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not strip transcript for %s: %s", video_id, exc)


def delete(video_id: str) -> None:
    """Remove a video from disk and the index."""
    path = _video_path(video_id)
    if path.exists():
        path.unlink()
    with _lock:
        _index.pop(video_id, None)
        _save_index()
    logger.info("Deleted cache for video_id=%s", video_id)


def list_ids() -> list[str]:
    """Return all cached video IDs."""
    return list(_index.keys())


_load_index()
