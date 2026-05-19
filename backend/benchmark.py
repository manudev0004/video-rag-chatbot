"""
CLI benchmark: compare sequential vs parallel video ingestion.

Usage:
    python -m backend.benchmark
    python -m backend.benchmark https://youtu.be/abc https://youtu.be/xyz
"""

import sys
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv

from .monitoring import probe_host, system_info, recent_metrics
from .services.transcript_service import get_transcript, extract_video_id
from .services.metadata_service import get_video_metadata
from .services.ingestion_service import chunk_transcript, store_chunks

load_dotenv()
logging.basicConfig(level=logging.WARNING)

DEMO_URLS = [
    "https://www.youtube.com/watch?v=ksn5yrsC3Wg",
    "https://www.youtube.com/watch?v=12DWqKQ6KHw",
]

NETWORK_TARGETS = [
    ("api.groq.com", 443),
    ("generativelanguage.googleapis.com", 443),
    ("www.youtube.com", 443),
]


def ingest_one(url: str) -> tuple[str, float]:
    """Ingest a single video and return (video_id, elapsed_seconds)."""
    t0 = time.perf_counter()
    transcript = get_transcript(url)
    metadata = get_video_metadata(url)
    chunks = chunk_transcript(transcript, {
        "video_id": metadata["video_id"],
        "title": metadata["title"],
        "creator": metadata["creator"],
        "engagement_rate": metadata["engagement_rate"],
    })
    store_chunks(chunks)
    return metadata["video_id"], round(time.perf_counter() - t0, 2)


def run_sequential(urls: list[str]) -> tuple[list[tuple[str, float]], float]:
    """Ingest videos one at a time. Returns per-video results and total time."""
    t0 = time.perf_counter()
    results = [ingest_one(url) for url in urls]
    return results, round(time.perf_counter() - t0, 2)


def run_parallel(urls: list[str]) -> tuple[list[tuple[str, float]], float]:
    """Ingest videos concurrently. Returns per-video results and total time."""
    t0 = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        futures = {pool.submit(ingest_one, url): url for url in urls}
        for fut in as_completed(futures):
            results.append(fut.result())
    return results, round(time.perf_counter() - t0, 2)


def probe_network() -> dict[str, float | None]:
    """Measure TCP connect time to each upstream API."""
    return {host: probe_host(host, port) for host, port in NETWORK_TARGETS}


def print_row(label: str, value: str, width: int = 28) -> None:
    print(f"  {label:<{width}} {value}")


def main(urls: list[str]) -> None:
    info = system_info()
    print("\nSystem")
    print_row("CPU cores", str(info["cpu_cores"]))
    print_row("CPU usage", f"{info['cpu_percent']}%")
    print_row("RAM free / total", f"{info['ram_free_gb']} GB / {info['ram_total_gb']} GB")

    print("\nNetwork latency (TCP connect)")
    latencies = probe_network()
    for host, ms in latencies.items():
        val = f"{ms:.1f} ms" if ms is not None else "unreachable"
        print_row(host, val)

    avg_latency = None
    reachable = [v for v in latencies.values() if v is not None]
    if reachable:
        avg_latency = sum(reachable) / len(reachable)
        print_row("average", f"{avg_latency:.1f} ms")

    print(f"\nIngesting {len(urls)} video(s) sequentially...")
    seq_results, seq_total = run_sequential(urls)

    print(f"Ingesting {len(urls)} video(s) in parallel...")
    par_results, par_total = run_parallel(urls)

    speedup = round(seq_total / par_total, 2) if par_total > 0 else 1.0

    print("\nIngestion results")
    print_row("sequential total", f"{seq_total}s")
    for vid, t in seq_results:
        print_row(f"  {vid}", f"{t}s")
    print_row("parallel total", f"{par_total}s")
    for vid, t in par_results:
        print_row(f"  {vid}", f"{t}s")
    print_row("speedup", f"{speedup}x")

    print("\nPer-operation metrics (from this run)")
    metrics = recent_metrics()
    if not metrics:
        print("  No metrics recorded.")
    else:
        for m in metrics[-20:]:
            hint = f" [{m.bottleneck_hint}]" if m.bottleneck_hint != "none" else ""
            print_row(
                m.name,
                f"{m.duration_ms:.0f}ms  score={m.efficiency_score:.1f}{hint}",
            )

    print("\nInterpretation")
    if avg_latency is not None and avg_latency > 300:
        print("  Network latency is high (>300ms). Slow operations are likely API-bound,")
        print("  not a code problem.")
    elif avg_latency is not None and avg_latency < 80:
        print("  Network latency looks healthy (<80ms).")

    if speedup >= 1.5:
        print(f"  Parallel ingestion is {speedup}x faster. For multiple videos, use parallel.")
    else:
        print(f"  Parallel speedup is {speedup}x. Network is the bottleneck, not concurrency.")

    low_score = [m for m in metrics if m.efficiency_score < 30]
    if low_score:
        names = ", ".join(set(m.name for m in low_score))
        print(f"  Low efficiency score (<30) on: {names}.")
        print("  This means the operation is slow relative to available hardware.")
        print("  Check network probes above to distinguish API latency from local slowness.")


if __name__ == "__main__":
    urls = sys.argv[1:] if len(sys.argv) > 1 else DEMO_URLS
    main(urls)
