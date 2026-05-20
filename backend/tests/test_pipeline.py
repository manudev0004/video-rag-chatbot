import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

from backend.services.transcript_service import get_transcript
from backend.services.metadata_service import get_video_metadata
from backend.services.ingestion_service import chunk_transcript, store_chunks
from backend.services.rag_service import app as rag_app
from backend.monitoring import recent_metrics, system_info


DEFAULT_URLS = [
    "https://www.youtube.com/watch?v=SVTPv4sI_Jc",
    "https://www.youtube.com/watch?v=B3m3AMRlYfc",
]


def ingest_video(url: str) -> dict | None:
    """Fetch transcript and metadata for a video, chunk and embed into ChromaDB.

    Returns metadata dict on success, None on failure.
    """
    logger.info("Ingesting %s", url)
    try:
        transcript = get_transcript(url)
    except RuntimeError as exc:
        logger.error("Transcript failed for %s: %s", url, exc)
        return None
    try:
        metadata = get_video_metadata(url)
    except (ValueError, RuntimeError) as exc:
        logger.error("Metadata failed for %s: %s", url, exc)
        return None
    chunks = chunk_transcript(transcript, {
        "video_id": metadata["video_id"],
        "title": metadata["title"],
        "creator": metadata["creator"],
        "engagement_rate": metadata["engagement_rate"],
    })
    if not chunks:
        logger.warning("No chunks produced for %s, transcript may be empty", url)
        return None
    stored = store_chunks(chunks)
    logger.info("Stored %d chunks for video_id=%s", stored, metadata["video_id"])
    return metadata


def main(urls: list[str]) -> None:
    """Run the full pipeline: ingest videos, ask a question, print results."""
    metas = []
    for url in urls:
        meta = ingest_video(url)
        if meta:
            metas.append(meta)

    if not metas:
        print("No videos ingested successfully. Exiting.")
        return

    question = (
        "Which video has better engagement and why?"
        if len(metas) >= 2
        else "What is this video mainly about and who is the target audience?"
    )

    result = rag_app.invoke({
        "question": question,
        "chat_history": [],
        "video_ids": [m["video_id"] for m in metas],
        "contexts": [],
        "sources": [],
        "answer": "",
    })

    print(f"\nQuestion: {question}")
    print("\nAnswer:")
    print(result["answer"])

    if result.get("sources"):
        print("\nSources:")
        for s in result["sources"]:
            print(f"  [{s['video_id']}] {s['title']} - chunk {s['chunk_index']} (dist={s['distance']})")


def print_metrics() -> None:
    """Print a summary of all tracked operations from this run."""
    info = system_info()
    print(f"\nSystem: {info['cpu_cores']} cores  CPU {info['cpu_percent']}%  RAM free {info['ram_free_gb']}GB / {info['ram_total_gb']}GB")

    ops = recent_metrics()
    if not ops:
        print("No metrics recorded.")
        return

    col = 22
    print(f"\n{'Operation':<{col}} {'Duration':>10} {'Score':>7} {'Bottleneck':>12} {'Mem delta':>10}")
    print("-" * (col + 44))
    for m in ops:
        hint = m.bottleneck_hint if m.bottleneck_hint != "none" else ""
        status = "ok" if m.success else "FAIL"
        print(f"{m.name:<{col}} {m.duration_ms:>9.0f}ms {m.efficiency_score:>7.1f} {hint:>12} {m.mem_delta_mb:>8.1f}MB  {status}")

    scores = [m.efficiency_score for m in ops]
    avg = sum(scores) / len(scores)
    print(f"\nAvg efficiency score: {avg:.1f} / 100")
    print("Score guide: 60+ good, 30-60 moderate, <30 likely network/API bound")


if __name__ == "__main__":
    show_metrics = "--metrics" in sys.argv
    urls = [a for a in sys.argv[1:] if not a.startswith("--")] or DEFAULT_URLS
    main(urls)
    if show_metrics:
        print_metrics()
