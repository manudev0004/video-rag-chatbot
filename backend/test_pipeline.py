import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


VIDEO_A_URL = "https://www.youtube.com/watch?v=SVTPv4sI_Jc"
VIDEO_B_URL = "https://www.youtube.com/watch?v=B3m3AMRlYfc"


def ingest_video(url: str) -> dict:
    """Fetch transcript and metadata for a video, chunk and embed into ChromaDB."""
    logger.info("Ingesting %s", url)
    transcript = get_transcript(url)
    metadata = get_video_metadata(url)
    chunks = chunk_transcript(transcript, {
        "video_id": metadata["video_id"],
        "title": metadata["title"],
        "creator": metadata["creator"],
        "engagement_rate": metadata["engagement_rate"],
    })
    stored = store_chunks(chunks)
    logger.info("Stored %d chunks for video_id=%s", stored, metadata["video_id"])
    return metadata


def main() -> None:
    """Run the full pipeline: ingest two videos, ask one question, print results."""
    meta_a = ingest_video(VIDEO_A_URL)
    meta_b = ingest_video(VIDEO_B_URL)

    result = rag_app.invoke({
        "question": "Which video has better engagement and why?",
        "chat_history": [],
        "video_ids": [meta_a["video_id"], meta_b["video_id"]],
        "contexts": [],
        "sources": [],
        "answer": "",
    })

    print("\nAnswer:")
    print(result["answer"])

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
    main()
    if show_metrics:
        print_metrics()
