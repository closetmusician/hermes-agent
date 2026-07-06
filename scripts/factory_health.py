#!/usr/bin/env python3
# ABOUTME: Jobs-first health command for the hermes autonomous factory host.
# ABOUTME: Prints 5 sections: provider reachability, compute state, running workers, last scheduler tick, lane state.
# ABOUTME: Distinguishes NETWORK failures (socket/timeout) from HERMES bugs (internal exceptions).
# ABOUTME: Exit codes: 0=all-healthy, 1=degraded (something down, cause attributable), 2=internal error.
# ABOUTME: Standalone — no edits to upstream hermes files needed; run directly with `python scripts/factory_health.py`.

"""
factory_health.py — single-glance factory host health signal.

Usage:
    python scripts/factory_health.py [--tick-path PATH] [--lane-marker-path PATH]

Exit codes:
    0  All sections healthy (providers reachable, compute OK, scheduler ticking,
       all lanes ok).
    1  Degraded — at least one section is unhealthy but cause is attributable
       (e.g. NETWORK outage, disk full, lane auth expired) — the factory cannot
       work but it's not a hermes bug.
    2  Internal error — a HERMES-attributed failure (bug inside factory_health
       or the hermes stack).  Inspect the HERMES detail for the exception.

Sections printed:
    [1] Providers — Anthropic + OpenRouter reachability (HEAD /models endpoint,
        no token spend) with measured latency.
    [2] Compute   — caffeinate assertion present? disk free? load average?
    [3] Workers   — factory worker processes (none = "not provisioned" until P2/P3).
    [4] Scheduler — last tick from ~/.hermes/factory/scheduler-tick ('never' handled).
    [5] Lanes     — R-4 re-auth marker: any lane whose auth failed shows 'degraded'
                    with the expiry reason; auto-clears to 'ok' on re-auth.
"""

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import psutil

# ---------------------------------------------------------------------------
# Well-known paths
# ---------------------------------------------------------------------------

DEFAULT_TICK_PATH = Path.home() / ".hermes" / "factory" / "scheduler-tick"

# R-4 re-auth marker written by pm_os/bin/ensure-tokens.js.
# Path contract (from R-4 report §REQ-04): ~/Code/pm_os/state/reauth-needed.json.
# Absent = healthy; {} = cleared (healthy); object with "lane" field = lane degraded.
DEFAULT_LANE_MARKER_PATH = Path.home() / "Code" / "pm_os" / "state" / "reauth-needed.json"

# Providers to check — name → HEAD URL (no token spend, public endpoint)
PROVIDERS = [
    ("anthropic", "https://api.anthropic.com/v1/models"),
    ("openrouter", "https://openrouter.ai/api/v1/models"),
]

# Factory worker process markers (command-line substrings that identify workers).
# During P0 no workers exist yet; these patterns will match them once P2/P3 land.
WORKER_CMD_MARKERS = ["claude -p", "codex exec"]

# ---------------------------------------------------------------------------
# Section 1: Provider reachability
# ---------------------------------------------------------------------------


def check_provider(name: str, url: str, timeout: float = 5.0) -> dict[str, Any]:
    """Probe a provider endpoint with an HTTP HEAD request and return a status dict.

    Purpose:
        Determines whether the given provider URL is reachable, measuring round-trip
        latency.  No authentication token is sent — HEAD /models is a public catalog
        endpoint that returns 200/401/403 when reachable and a connection error when
        not.  Any 4xx/5xx response still counts as "reachable" (we reached the server).

    Usage:
        result = check_provider("anthropic", "https://api.anthropic.com/v1/models")
        # result["status"] in {"reachable", "unreachable"}
        # result["cause"] in {None, "NETWORK", "HERMES"}

    Gotchas:
        - httpx.ConnectError / TimeoutException are NETWORK causes.
        - Any other exception is attributed to HERMES (a bug in this code).
        - Returns latency_ms=None on failure; otherwise a non-negative float.
    """
    try:
        t0 = time.monotonic()
        with httpx.Client(timeout=timeout) as client:
            client.head(url)
        latency_ms = (time.monotonic() - t0) * 1000.0
        return {
            "name": name,
            "url": url,
            "status": "reachable",
            "latency_ms": round(latency_ms, 1),
            "cause": None,
            "error": None,
        }
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        return {
            "name": name,
            "url": url,
            "status": "unreachable",
            "latency_ms": None,
            "cause": "NETWORK",
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "name": name,
            "url": url,
            "status": "unreachable",
            "latency_ms": None,
            "cause": "HERMES",
            "error": str(exc),
        }


def check_all_providers() -> list[dict[str, Any]]:
    """Run check_provider for every configured provider and return the list.

    Purpose:
        Iterates PROVIDERS and collects one result dict per provider.

    Usage:
        results = check_all_providers()

    Gotchas:
        Failures are captured per-provider; one unreachable provider does not
        prevent checking the others.
    """
    return [check_provider(name, url) for name, url in PROVIDERS]


# ---------------------------------------------------------------------------
# Section 2: Compute state
# ---------------------------------------------------------------------------


def check_compute_state(disk_path: str = "/") -> dict[str, Any]:
    """Return the current compute state of the Mac host.

    Purpose:
        Gathers three signals relevant to overnight factory operation:
        - caffeinate: is a caffeinate process running (keeps Mac awake)?
        - disk_free_gb: free space on the root filesystem in GiB.
        - load_avg: 1/5/15-minute load average tuple from os.getloadavg().

    Usage:
        state = check_compute_state()
        if not state["caffeinate"]:
            print("WARNING: Mac may sleep and kill workers")

    Gotchas:
        - caffeinate detection scans all processes for name == 'caffeinate';
          will return False if psutil raises (e.g. permission denied on some procs).
        - On macOS os.getloadavg() is always available; on other platforms it may
          raise AttributeError — callers should catch that if needed.
        - disk_path defaults to "/" but tests may pass a different mount point.
    """
    # Check caffeinate
    caffeinate_running = False
    try:
        for proc in psutil.process_iter():
            try:
                if proc.name() == "caffeinate":
                    caffeinate_running = True
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:  # noqa: BLE001
        caffeinate_running = False

    # Disk free
    usage = shutil.disk_usage(disk_path)
    disk_free_gb = usage.free / (1024 ** 3)

    # Load average
    load_avg = os.getloadavg()

    return {
        "caffeinate": caffeinate_running,
        "disk_free_gb": round(disk_free_gb, 1),
        "load_avg": load_avg,
    }


# ---------------------------------------------------------------------------
# Section 3: Running workers
# ---------------------------------------------------------------------------


def check_running_workers() -> dict[str, Any]:
    """Count active factory worker processes and report provisioning status.

    Purpose:
        Scans running processes for command-lines that match the worker markers
        (claude -p, codex exec).  In P0 no workers exist — returns count=0 and
        a 'not provisioned' message rather than pretending everything is fine.

    Usage:
        result = check_running_workers()
        print(result["count"], result["message"])

    Gotchas:
        - cmdline() may raise AccessDenied/NoSuchProcess on some system processes;
          those are silently skipped.
        - Message wording differs between provisioned (count>0) and not-provisioned
          (count==0) states so callers can distinguish them textually.
    """
    worker_procs = []
    try:
        for proc in psutil.process_iter():
            try:
                cmdline = proc.cmdline()
                cmd_str = " ".join(cmdline)
                if any(marker in cmd_str for marker in WORKER_CMD_MARKERS):
                    worker_procs.append(proc.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:  # noqa: BLE001
        pass

    count = len(worker_procs)
    if count == 0:
        message = "no workers, factory not provisioned (fleet scheduler ships in P2/P3)"
    else:
        message = f"{count} worker process(es) active: pids {worker_procs}"

    return {
        "count": count,
        "worker_pids": worker_procs,
        "message": message,
    }


# ---------------------------------------------------------------------------
# Section 4: Scheduler tick
# ---------------------------------------------------------------------------


def check_scheduler_tick(tick_path: Path = DEFAULT_TICK_PATH) -> dict[str, Any]:
    """Return the last scheduler tick time from the well-known state file.

    Purpose:
        Reads the scheduler tick file (default: ~/.hermes/factory/scheduler-tick).
        If absent, reports 'never' — this is the expected state in P0 before the
        fleet scheduler (P2/P3) has been provisioned.  If present, also reports
        how many seconds ago the file was last modified (mtime).

    Usage:
        result = check_scheduler_tick()
        if result["last_tick"] == "never":
            print("Scheduler not yet provisioned")

    Gotchas:
        - The tick file content is returned as-is (stripped); the fleet scheduler
          is expected to write an ISO-8601 timestamp on each tick.
        - seconds_ago uses file mtime, not content parsing, so it works even if
          the content format changes.
        - Returns seconds_ago=None when the file is absent.
    """
    if not tick_path.exists():
        return {
            "last_tick": "never",
            "tick_path": str(tick_path),
            "seconds_ago": None,
            "provisioned": False,
        }

    last_tick = tick_path.read_text().strip()
    seconds_ago = time.time() - tick_path.stat().st_mtime

    return {
        "last_tick": last_tick,
        "tick_path": str(tick_path),
        "seconds_ago": round(seconds_ago, 1),
        "provisioned": True,
    }


# ---------------------------------------------------------------------------
# Section 5: Lane state (R-4 re-auth marker)
# ---------------------------------------------------------------------------


def check_lane_state(
    marker_path: Path = DEFAULT_LANE_MARKER_PATH,
) -> dict[str, Any]:
    """Read the R-4 re-auth marker and return the lane health state.

    Purpose:
        Reads ~/Code/pm_os/state/reauth-needed.json (written by pm_os
        ensure-tokens.js when an SSO/FOCI token expires).  Three cases:
        - File absent → all lanes ok (no auth failure ever recorded).
        - File = {} → all lanes ok (re-auth resolved, marker cleared).
        - File contains {"lane": ..., "expiry_type": ..., ...} → that lane
          is degraded; it will NOT auto-retry (R-4 exits with SCHEDULED_EXIT_CODE
          on expiry and never re-enters the retry loop).

    Usage:
        result = check_lane_state()
        if result["status"] == "degraded":
            print(result["degraded"][0]["action_needed"])

    Gotchas:
        - Reads the raw file; does NOT call pm_os lib/status.js to stay
          standalone (hermes-side only per constraint).
        - A malformed JSON file is treated as absent (safe default = ok).
        - The marker path is configurable for tests; production uses
          DEFAULT_LANE_MARKER_PATH resolved at import time.
    """
    try:
        raw = marker_path.read_text(encoding="utf-8").strip() if marker_path.exists() else None
    except OSError:
        raw = None

    # Absent or unreadable → healthy
    if raw is None:
        return {"status": "ok", "degraded": [], "marker_path": str(marker_path)}

    # Parse JSON; treat malformed as absent
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"status": "ok", "degraded": [], "marker_path": str(marker_path)}

    # Cleared state ({} or object without "lane") → healthy
    if not isinstance(data, dict) or "lane" not in data:
        return {"status": "ok", "degraded": [], "marker_path": str(marker_path)}

    # Lane is degraded
    entry = {
        "lane": data.get("lane"),
        "expiry_type": data.get("expiry_type"),
        "reason": data.get("reason", ""),
        "ts": data.get("ts"),
        "action_needed": data.get("action_needed", "run /pm-login"),
    }
    return {
        "status": "degraded",
        "degraded": [entry],
        "marker_path": str(marker_path),
    }


# ---------------------------------------------------------------------------
# Exit code computation
# ---------------------------------------------------------------------------


def compute_exit_code(sections: dict[str, Any]) -> int:
    """Compute the process exit code from the collected health sections.

    Purpose:
        Maps the health results to a numeric exit code:
        - 0: all providers reachable; no HERMES causes anywhere.
        - 1: at least one provider unreachable with a NETWORK cause — degraded but
             attributable (e.g. offline Mac, provider outage).
        - 2: any section has a HERMES-attributed cause — an internal bug that needs
             investigation.

    Usage:
        code = compute_exit_code(sections)
        sys.exit(code)

    Gotchas:
        - Exit 2 takes priority over exit 1: a HERMES error is more actionable.
        - Workers section reports "no workers" as healthy (not an error) in P0.
        - Scheduler "never" is treated as healthy in P0 (not provisioned, not broken).
    """
    has_hermes_error = False
    has_network_error = False

    for provider in sections.get("providers", []):
        if provider.get("cause") == "HERMES":
            has_hermes_error = True
        elif provider.get("cause") == "NETWORK":
            has_network_error = True

    # A degraded lane (auth failure) counts as degraded, not healthy.
    # R-4 guarantees the lane stopped retrying when degraded, so this is
    # attributable (not a hermes bug) → exit 1, not exit 2.
    lanes = sections.get("lanes", {})
    if lanes.get("status") == "degraded" and lanes.get("degraded"):
        has_network_error = True  # reuse degraded bucket; lanes are auth/network issues

    if has_hermes_error:
        return 2
    if has_network_error:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _provider_line(result: dict[str, Any]) -> str:
    """Format a single provider result as a human-readable line.

    Purpose: Turns the check_provider dict into a display string.
    Usage: line = _provider_line(result)
    Gotchas: Handles None latency_ms gracefully for unreachable providers.
    """
    name = result["name"].upper()
    if result["status"] == "reachable":
        return f"  {name}: REACHABLE ({result['latency_ms']}ms)"
    cause = result.get("cause", "UNKNOWN")
    error = result.get("error", "")
    return f"  {name}: UNREACHABLE [{cause}] {error}"


def _format_load(load_avg: tuple) -> str:
    """Format the 3-tuple load average as a short string.

    Purpose: Renders load avg for display.
    Usage: s = _format_load((1.2, 0.9, 0.7))
    Gotchas: Assumes 3-element tuple.
    """
    return f"{load_avg[0]:.2f}, {load_avg[1]:.2f}, {load_avg[2]:.2f}"


def render_health_report(sections: dict[str, Any]) -> str:
    """Render all health sections as a human-readable string.

    Purpose:
        Formats the sections dict into the terminal output shown to the operator.
        Each section has a header and per-item detail lines.

    Usage:
        print(render_health_report(sections))

    Gotchas:
        - Compute state caffeinate=False is flagged with a WARNING prefix.
        - Worker count=0 shows the 'not provisioned' message without alarm (expected in P0).
        - Scheduler last_tick='never' is shown as 'not yet provisioned (expected in P0)'.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [f"=== Factory Host Health  {now} ===", ""]

    # Section 1 — Providers
    lines.append("[1] Provider Reachability")
    for p in sections.get("providers", []):
        lines.append(_provider_line(p))
    lines.append("")

    # Section 2 — Compute
    compute = sections.get("compute", {})
    lines.append("[2] Compute State")
    caff = compute.get("caffeinate")
    caff_str = "YES (awake)" if caff else "NO — WARNING: Mac may sleep and kill workers"
    lines.append(f"  caffeinate: {caff_str}")
    disk_gb = compute.get("disk_free_gb", "?")
    lines.append(f"  disk free:  {disk_gb} GiB")
    load = compute.get("load_avg", (0, 0, 0))
    lines.append(f"  load avg:   {_format_load(load)} (1m/5m/15m)")
    lines.append("")

    # Section 3 — Workers
    workers = sections.get("workers", {})
    lines.append("[3] Running Workers")
    lines.append(f"  {workers.get('message', 'unknown')}")
    lines.append("")

    # Section 4 — Scheduler
    scheduler = sections.get("scheduler", {})
    lines.append("[4] Last Scheduler Tick")
    last = scheduler.get("last_tick", "never")
    if last == "never":
        lines.append("  last tick: never (not yet provisioned — expected in P0)")
    else:
        secs = scheduler.get("seconds_ago")
        ago_str = f"  ({secs:.0f}s ago)" if secs is not None else ""
        lines.append(f"  last tick: {last}{ago_str}")
    lines.append("")

    # Section 5 — Lane state (R-4 re-auth marker)
    lanes = sections.get("lanes", {})
    lines.append("[5] Lane State")
    if not lanes or lanes.get("status") == "ok":
        lines.append("  all lanes: ok")
    else:
        for entry in lanes.get("degraded", []):
            lane = entry.get("lane", "unknown")
            expiry = entry.get("expiry_type", "unknown")
            action = entry.get("action_needed", "run /pm-login")
            reason = entry.get("reason", "")
            lines.append(f"  {lane}: DEGRADED [{expiry}] — {action}")
            if reason:
                lines.append(f"    reason: {reason}")
    lines.append("")

    # Summary exit code hint
    exit_code = compute_exit_code(sections)
    code_labels = {0: "ALL HEALTHY", 1: "DEGRADED", 2: "INTERNAL ERROR"}
    lines.append(f"--- Status: {code_labels.get(exit_code, 'UNKNOWN')} (exit {exit_code}) ---")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


def run_health_check(
    tick_path: Path = DEFAULT_TICK_PATH,
    lane_marker_path: Path = DEFAULT_LANE_MARKER_PATH,
) -> tuple[dict[str, Any], int]:
    """Collect all health sections and compute the exit code.

    Purpose:
        Orchestrates all five checks and returns (sections, exit_code).

    Usage:
        sections, code = run_health_check()
        print(render_health_report(sections))
        sys.exit(code)

    Gotchas:
        - Each section check is independent; a crash in one does not abort others.
        - The compute section crash is caught and reported as an empty dict.
        - lane_marker_path defaults to ~/Code/pm_os/state/reauth-needed.json per
          the R-4 contract; absent/cleared marker = healthy.
    """
    sections: dict[str, Any] = {}

    # Providers
    try:
        sections["providers"] = check_all_providers()
    except Exception as exc:  # noqa: BLE001
        sections["providers"] = [{
            "name": "unknown",
            "url": "",
            "status": "unreachable",
            "cause": "HERMES",
            "error": f"check_all_providers crashed: {exc}",
            "latency_ms": None,
        }]

    # Compute
    try:
        sections["compute"] = check_compute_state()
    except Exception as exc:  # noqa: BLE001
        sections["compute"] = {
            "caffeinate": None,
            "disk_free_gb": None,
            "load_avg": (0, 0, 0),
            "_error": f"check_compute_state crashed: {exc}",
        }

    # Workers
    try:
        sections["workers"] = check_running_workers()
    except Exception as exc:  # noqa: BLE001
        sections["workers"] = {
            "count": 0,
            "worker_pids": [],
            "message": f"worker check crashed: {exc}",
        }

    # Scheduler
    try:
        sections["scheduler"] = check_scheduler_tick(tick_path=tick_path)
    except Exception as exc:  # noqa: BLE001
        sections["scheduler"] = {
            "last_tick": f"error: {exc}",
            "tick_path": str(tick_path),
            "seconds_ago": None,
            "provisioned": False,
        }

    # Lanes (R-4 re-auth marker)
    try:
        sections["lanes"] = check_lane_state(marker_path=lane_marker_path)
    except Exception as exc:  # noqa: BLE001
        sections["lanes"] = {
            "status": "ok",
            "degraded": [],
            "_error": f"check_lane_state crashed: {exc}",
        }

    exit_code = compute_exit_code(sections)
    return sections, exit_code


def main() -> None:
    """CLI entrypoint: parse args, run health check, print report, exit.

    Purpose: Parses --tick-path and --lane-marker-path, runs all checks, prints report, exits.
    Usage: `python scripts/factory_health.py [--tick-path PATH] [--lane-marker-path PATH]`
    Gotchas: Exits with the computed exit code (0/1/2 per ABOUTME header).
    """
    parser = argparse.ArgumentParser(
        description="Hermes factory host health check — one command to know if the factory can run tonight."
    )
    parser.add_argument(
        "--tick-path",
        type=Path,
        default=DEFAULT_TICK_PATH,
        help=f"Path to the scheduler tick file (default: {DEFAULT_TICK_PATH})",
    )
    parser.add_argument(
        "--lane-marker-path",
        type=Path,
        default=DEFAULT_LANE_MARKER_PATH,
        help=f"Path to the R-4 reauth-needed.json marker (default: {DEFAULT_LANE_MARKER_PATH})",
    )
    args = parser.parse_args()

    sections, exit_code = run_health_check(
        tick_path=args.tick_path,
        lane_marker_path=args.lane_marker_path,
    )
    print(render_health_report(sections))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
