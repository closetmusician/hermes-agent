# ABOUTME: factory/watchdogs — self-supervision watchdog package (P5-d).
# ABOUTME: Plain-code observers that ENFORCE (pause/revert/throttle), never advisory.
# ABOUTME: Five modules: drift (pause churning workers), regression_sentinel (revert
# ABOUTME: post-merge breaks), memory_zone (GREEN/YELLOW/RED throttle), watchdog_supervisor
# ABOUTME: (fail-safe if a watchdog dies), worktree_gc (disk pre-flight + stale reap).
"""
factory.watchdogs — self-supervision watchdogs for the Fable factory (P5-d).

No AI in any control path.  Each watchdog is a plain callable that the scheduler
(or its host) invokes each tick; they observe the running fleet and ENFORCE actions
(pause, revert, throttle) rather than emitting advisory log lines.
"""
from factory.watchdogs.drift import DriftWatchdog
from factory.watchdogs.memory_zone import MemoryZone
from factory.watchdogs.regression_sentinel import RegressionSentinel
from factory.watchdogs.watchdog_supervisor import WatchdogSupervisor
from factory.watchdogs.worktree_gc import WorktreeGC

__all__ = [
    "DriftWatchdog",
    "MemoryZone",
    "RegressionSentinel",
    "WatchdogSupervisor",
    "WorktreeGC",
]
