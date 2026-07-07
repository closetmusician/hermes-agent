# ABOUTME: Memory-zone watchdog for the Fable factory — GREEN/YELLOW/RED based on
# ABOUTME: host RSS/free-memory.  Exposes throttled() → bool wired into the scheduler's
# ABOUTME: throttled_fn seam.  RED ⇒ throttled()=True ⇒ no new spawns; YELLOW caps
# ABOUTME: concurrency at 2-3; GREEN ⇒ full cap.  Composes with budget throttle by OR.
# ABOUTME: Design source: docs/plans/harness/fable/p5/P5-design.md §5.3
"""
Memory-zone watchdog (P5-d §5.3) — read host free memory each tick and expose a
throttled() callable that the scheduler's throttled_fn seam accepts.

  GREEN   (free ≥ GREEN_FLOOR_MB)  → throttled()=False, full concurrency
  YELLOW  (YELLOW_FLOOR_MB ≤ free < GREEN_FLOOR_MB)  → throttled()=False but
           cap override is available via yellow_cap(); let scheduler set lower cap
  RED     (free < YELLOW_FLOOR_MB) → throttled()=True  → scheduler admits nothing new

The throttled() callable is designed for the OR composition the scheduler uses:
  throttled_fn = lambda: memory_zone.throttled() or disk_gc.disk_full()
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Default thresholds (MB of free memory).
GREEN_FLOOR_MB = 1024    # ≥1 GB free → full cap
YELLOW_FLOOR_MB = 512    # ≥512 MB free → yellow (soft cap)
# Below YELLOW_FLOOR_MB → RED → throttled()=True

YELLOW_CAP = 2  # max concurrent spawns in YELLOW zone

# Zone string values used in zone() return and in test assertions.
ZONE_GREEN = "GREEN"
ZONE_YELLOW = "YELLOW"
ZONE_RED = "RED"


def _free_mb_default() -> float:
    """
    Purpose: read the host's available memory in megabytes from /proc/meminfo or
    psutil (whichever is available).
    Usage: internal default memory reader.
    Gotchas: falls back to a very large value on any read error (fail-open for
    memory reading — a broken reader should not block all spawns, the disk_gc is
    the harder floor gate).
    """
    try:
        # Linux: /proc/meminfo MemAvailable is the most accurate free-memory figure.
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb / 1024.0
    except (OSError, ValueError, IndexError):
        pass

    try:
        import psutil
        mem = psutil.virtual_memory()
        return mem.available / (1024 * 1024)
    except Exception:
        pass

    # Can't read memory — fail-open (don't block spawns for a sensor failure).
    logger.warning("MemoryZone: cannot read free memory — assuming GREEN")
    return float("inf")


class MemoryZone:
    """
    Purpose: GREEN/YELLOW/RED memory gate wired into the scheduler's throttled_fn.
    Exposes throttled() → bool (RED ⇒ True) and zone() → 'GREEN'|'YELLOW'|'RED'.

    Usage:
        mz = MemoryZone()
        scheduler = Scheduler(..., throttled_fn=mz.throttled)
        # or composed: throttled_fn = lambda: mz.throttled() or gc.disk_full()

    Gotchas:
      * memory_reader is injectable (tests inject a lambda returning a fixed value).
      * RED throttle is the strongest level: no new spawns regardless of concurrency cap.
      * YELLOW is surfaced via yellow_cap(); the scheduler may choose to lower its cap
        in response, but throttled() itself stays False in YELLOW.
    """

    def __init__(
        self,
        *,
        memory_reader: Optional[Callable[[], float]] = None,
        green_floor_mb: float = GREEN_FLOOR_MB,
        yellow_floor_mb: float = YELLOW_FLOOR_MB,
        yellow_cap: int = YELLOW_CAP,
    ):
        self._read_memory = memory_reader or _free_mb_default
        self._green_floor = green_floor_mb
        self._yellow_floor = yellow_floor_mb
        self._yellow_cap = yellow_cap

        # Heartbeat for watchdog-supervisor health checks.
        self._last_run_ts: float = 0.0

    def zone(self) -> str:
        """
        Purpose: return the current memory zone string ('GREEN', 'YELLOW', or 'RED').
        Usage: z = mz.zone()
        Gotchas: reads memory each call; not cached (tests can change the reader).
        """
        self._last_run_ts = time.time()
        free_mb = self._read_memory()
        if free_mb >= self._green_floor:
            return ZONE_GREEN
        if free_mb >= self._yellow_floor:
            return ZONE_YELLOW
        return ZONE_RED

    def throttled(self) -> bool:
        """
        Purpose: the scheduler's throttled_fn callable.  Returns True (block spawns)
        when the zone is RED.  YELLOW and GREEN return False (scheduler may still
        enforce a lower cap via yellow_cap()).
        Usage: scheduler = Scheduler(..., throttled_fn=mz.throttled)
        Gotchas: this is a bound method — pass `mz.throttled` (not `mz.throttled()`).
        """
        return self.zone() == ZONE_RED

    def get_yellow_cap(self) -> int:
        """
        Purpose: the recommended concurrency cap when in YELLOW zone.
        Usage: if mz.zone() == YELLOW: cap = mz.get_yellow_cap()
        Gotchas: only relevant in YELLOW; callers should check zone() first.
        """
        return self._yellow_cap
