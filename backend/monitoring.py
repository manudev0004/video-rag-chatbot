import os
import math
import time
import socket
import logging
import functools
import psutil
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Any

logger = logging.getLogger(__name__)

# Holds the last 100 metrics recorded during the server's lifetime
_store: deque["Metric"] = deque(maxlen=100)


@dataclass
class Metric:
    name: str
    duration_ms: float
    cpu_percent: float
    mem_before_mb: float
    mem_after_mb: float
    success: bool
    tags: dict = field(default_factory=dict)

    @property
    def mem_delta_mb(self) -> float:
        return round(self.mem_after_mb - self.mem_before_mb, 2)

    @property
    def efficiency_score(self) -> float:
        """
        0-100 score: how fast this ran given current hardware capacity.

        High score = fast AND the machine had headroom.
        Low score = slow OR the machine was already loaded.
        Lets you distinguish "my code is slow" from "my machine was busy."
        """
        free_cpu = 1 - (self.cpu_percent / 100)
        free_ram_gb = psutil.virtual_memory().available / (1024 ** 3)
        cpu_cores = os.cpu_count() or 1
        # Log scale: 10ms~81, 100ms~63, 1s~45, 10s~27, 60s~15
        speed = max(0.0, 100 - math.log1p(self.duration_ms) * 8)
        hw = min(1.0, cpu_cores / 4) * min(1.0, (free_ram_gb + 2) / 8)
        return round(speed * free_cpu * hw, 1)

    @property
    def bottleneck_hint(self) -> str:
        if self.cpu_percent > 75:
            return "CPU"
        if self.mem_delta_mb > 50:
            return "memory"
        if self.efficiency_score < 30:
            return "network/API"
        return "none"


def track(name: str) -> Callable:
    """Decorator for sync functions: records timing, CPU load, and memory usage."""
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            proc = psutil.Process()
            mem_before = proc.memory_info().rss / (1024 ** 2)
            cpu_at_start = psutil.cpu_percent(interval=None)
            t0 = time.perf_counter()
            ok = True
            try:
                return fn(*args, **kwargs)
            except Exception:
                ok = False
                raise
            finally:
                m = Metric(
                    name=name,
                    duration_ms=round((time.perf_counter() - t0) * 1000, 2),
                    cpu_percent=cpu_at_start,
                    mem_before_mb=round(mem_before, 2),
                    mem_after_mb=round(proc.memory_info().rss / (1024 ** 2), 2),
                    success=ok,
                )
                _store.append(m)
                logger.info(
                    "METRIC %s %.0fms mem_delta=%.1fMB score=%.1f ok=%s",
                    m.name, m.duration_ms, m.mem_delta_mb, m.efficiency_score, m.success,
                )
        return wrapper
    return decorator


def system_info() -> dict:
    """Current hardware snapshot."""
    vm = psutil.virtual_memory()
    return {
        "cpu_cores": os.cpu_count(),
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "ram_total_gb": round(vm.total / (1024 ** 3), 1),
        "ram_free_gb": round(vm.available / (1024 ** 3), 1),
    }


def probe_host(host: str, port: int = 443, timeout: float = 3.0) -> float | None:
    """TCP connect time to host in milliseconds. Returns None if unreachable."""
    try:
        t0 = time.perf_counter()
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return round((time.perf_counter() - t0) * 1000, 1)
    except OSError:
        return None


def recent_metrics() -> list[Metric]:
    return list(_store)
