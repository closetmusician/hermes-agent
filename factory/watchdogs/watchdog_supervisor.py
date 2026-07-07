# ABOUTME: Watchdog-supervisor (P5-d §5.4) — a supervisor self-check that confirms
# ABOUTME: each watchdog ran recently, respawns a dead one, and fail-safes (throttles)
# ABOUTME: if the drift or sentinel watchdog cannot be confirmed alive.  Never advisory:
# ABOUTME: a dead watchdog → new spawns blocked until it revives.
# ABOUTME: Design source: docs/plans/harness/fable/p5/P5-design.md §5.4
"""
Watchdog-supervisor (P5-d §5.4) — the meta-watchdog that watches the watchdogs.

Each tick:
  1. Check each registered watchdog's _last_run_ts against WATCHDOG_STALE_S.
  2. If stale AND a respawn_fn is registered → respawn the watchdog.
  3. If the drift or sentinel watchdog is confirmed DEAD and un-respawnable →
     set _fail_safe=True → throttled() returns True (no new spawns).

The fail-safe is explicit: an unwatched fleet is more dangerous than a paused one.
A test must assert that throttled() returns True when a required watchdog is dead
and cannot be respawned — downgrading to a log line MUST make that test fail.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Watchdogs that MUST be alive — if either is confirmed dead and un-respawnable,
# the supervisor engages the fail-safe throttle.
REQUIRED_WATCHDOGS = {"drift", "sentinel"}

# How long a watchdog may be silent before it is considered stale.
WATCHDOG_STALE_S = 120  # 2 minutes


class WatchdogSupervisor:
    """
    Purpose: the meta-watchdog that confirms every watchdog is alive each tick,
    respawns dead ones, and fail-safes by throttling new spawns if a required
    watchdog cannot be confirmed.

    Usage:
        ws = WatchdogSupervisor()
        ws.register("drift", drift_wd, respawn_fn=lambda: DriftWatchdog(...))
        ws.register("sentinel", sent_wd)
        # Wire into scheduler:
        scheduler = Scheduler(..., throttled_fn=ws.throttled)
        # Each tick:
        ws.tick()

    Gotchas:
      * _fail_safe is set to True permanently once a required watchdog is dead and
        un-respawnable; only a successful respawn clears it.
      * The supervisor writes its own heartbeat (_last_run_ts) for higher-level
        monitoring (e.g. the job_store supervisor-lock stale-steal covers the main loop;
        this covers the watchdog threads specifically).
    """

    def __init__(self, stale_s: int = WATCHDOG_STALE_S):
        self._stale_s = stale_s
        self._watchdogs: Dict[str, Any] = {}         # name → watchdog object
        self._respawn_fns: Dict[str, Optional[Callable[[], Any]]] = {}
        self._lock = threading.Lock()
        self._fail_safe = False                       # True = throttle new spawns
        self._last_run_ts: float = 0.0

    def register(
        self,
        name: str,
        watchdog: Any,
        *,
        respawn_fn: Optional[Callable[[], Any]] = None,
    ) -> None:
        """
        Purpose: register a watchdog by name for supervision.
        Usage: ws.register("drift", drift_wd, respawn_fn=lambda: DriftWatchdog(...))
        Gotchas: respawn_fn should return a new watchdog object ready to tick; the
        supervisor replaces the registered object on a successful respawn.
        """
        with self._lock:
            self._watchdogs[name] = watchdog
            self._respawn_fns[name] = respawn_fn

    def tick(self) -> List[Dict[str, Any]]:
        """
        Purpose: one supervision pass.  Checks each watchdog's last_run_ts; respawns
        stale ones; engages fail-safe if a required watchdog is dead and unrevivable.
        Returns a list of event dicts (one per action taken).
        Usage: events = ws.tick()  # call each scheduler tick.
        Gotchas: the fail-safe persists across ticks until a successful respawn.
        A respawned watchdog must call tick() at least once before it clears the flag.
        """
        self._last_run_ts = time.time()
        events: List[Dict[str, Any]] = []

        with self._lock:
            watchdog_names = list(self._watchdogs.keys())

        for name in watchdog_names:
            with self._lock:
                wd = self._watchdogs.get(name)
                respawn_fn = self._respawn_fns.get(name)

            if wd is None:
                continue

            last_ts = getattr(wd, "_last_run_ts", None) or 0.0
            age_s = time.time() - last_ts

            if age_s <= self._stale_s:
                # Watchdog is alive — clear fail-safe if it was set for this name.
                if self._fail_safe and name in REQUIRED_WATCHDOGS:
                    # Re-evaluate: only clear if ALL required watchdogs are alive.
                    if self._all_required_alive():
                        logger.info(
                            "WatchdogSupervisor: %s recovered — clearing fail-safe", name
                        )
                        with self._lock:
                            self._fail_safe = False
                continue

            # Watchdog is stale.
            logger.warning(
                "WatchdogSupervisor: watchdog %r stale (age=%.0fs)", name, age_s
            )
            events.append({"watchdog": name, "event": "stale", "age_s": age_s})

            # Try to respawn.
            if respawn_fn is not None:
                try:
                    new_wd = respawn_fn()
                    with self._lock:
                        self._watchdogs[name] = new_wd
                    logger.info("WatchdogSupervisor: respawned watchdog %r", name)
                    events.append({"watchdog": name, "event": "respawned"})
                    # New watchdog starts with last_run_ts=0; clear fail-safe only
                    # after it proves itself alive (next tick where age ≤ stale_s).
                except Exception as exc:
                    logger.error(
                        "WatchdogSupervisor: respawn of %r failed: %s", name, exc
                    )
                    events.append({"watchdog": name, "event": "respawn_failed", "error": str(exc)})
                    if name in REQUIRED_WATCHDOGS:
                        logger.error(
                            "WatchdogSupervisor: required watchdog %r dead and unrevivable"
                            " — engaging fail-safe throttle", name
                        )
                        with self._lock:
                            self._fail_safe = True
            else:
                # No respawn function — if required, engage fail-safe.
                if name in REQUIRED_WATCHDOGS:
                    logger.error(
                        "WatchdogSupervisor: required watchdog %r stale with no respawn_fn"
                        " — engaging fail-safe throttle", name
                    )
                    with self._lock:
                        self._fail_safe = True

        return events

    def throttled(self) -> bool:
        """
        Purpose: the scheduler's throttled_fn callable for the fail-safe gate.
        Returns True when a required watchdog is dead and un-respawnable.
        Usage: scheduler = Scheduler(..., throttled_fn=ws.throttled)
        Gotchas: composes with MemoryZone via OR (pass a lambda that calls both).
        """
        with self._lock:
            return self._fail_safe

    def _all_required_alive(self) -> bool:
        """
        Purpose: check whether all REQUIRED_WATCHDOGS have run recently.
        Usage: internal; called before clearing the fail-safe flag.
        Gotchas: must be called without holding the lock (it acquires it).
        """
        for name in REQUIRED_WATCHDOGS:
            wd = self._watchdogs.get(name)
            if wd is None:
                return False
            last_ts = getattr(wd, "_last_run_ts", None) or 0.0
            if time.time() - last_ts > self._stale_s:
                return False
        return True

    def get_watchdog(self, name: str) -> Optional[Any]:
        """
        Purpose: retrieve a registered watchdog by name (useful for tests).
        Usage: wd = ws.get_watchdog("drift")
        Gotchas: returns None if the name is not registered.
        """
        with self._lock:
            return self._watchdogs.get(name)
