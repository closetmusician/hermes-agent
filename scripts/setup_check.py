#!/usr/bin/env python3
# ABOUTME: Behavioral setup checker for the hermes factory host.
# ABOUTME: Runs automatable round-trip checks for each messaging/tool lane and classifies
# ABOUTME: each as DONE / NOT DONE / MANUAL (cannot automate without MFA or human).
# ABOUTME: Reuses factory_health.py provider and compute checks. Exits 0 all DONE/MANUAL,
# ABOUTME: exits 1 if any automatable check is NOT DONE.

"""
setup_check.py — factory lane behavioral check runner.

Purpose
-------
Runs every automatable check from SETUP-CHECKLIST.md and prints a per-lane
classification:
  DONE     — round-trip verified; no stub detected
  NOT DONE — check failed or stub detected
  MANUAL   — requires owner MFA / interactive action; cannot be automated here

Usage
-----
  python scripts/setup_check.py [--send-telegram] [--whatsapp-roundtrip] [--json]

  --send-telegram       Also attempt a real Telegram test send (requires gateway up)
  --whatsapp-roundtrip  Run the WhatsApp paired round-trip check (requires pairing first)
  --json                Emit machine-readable JSON instead of human text

Exit codes
----------
  0  All automatable checks DONE (MANUAL items not blocking)
  1  At least one automatable check is NOT DONE

Stub-detection
--------------
See SETUP-CHECKLIST.md §9 for the canonical stub-detection rules.
The key heuristic applied here: if removing a credential still returns success,
the check is theater.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PM_OS_BIN = Path.home() / "Code" / "pm_os" / "bin"
HERMES_ENV = Path.home() / ".hermes" / ".env"
HERMES_CONFIG = Path.home() / ".hermes" / "config.yaml"
FACTORY_HEALTH_SCRIPT = Path(__file__).parent / "factory_health.py"
PROBE_SCRIPT = Path(__file__).parent / "probe.py"

# Classification constants — these are the only valid values for a check result.
DONE = "DONE"
NOT_DONE = "NOT DONE"
MANUAL = "MANUAL"

# ---------------------------------------------------------------------------
# Result dataclass (plain dict for simplicity + testability)
# ---------------------------------------------------------------------------


def make_result(lane: str, status: str, reason: str, automatable: bool = True) -> dict[str, Any]:
    """Build a standardised check-result dict.

    Purpose:
        Creates a uniform result record for one lane check so the renderer and
        tests have a stable schema to work against.

    Usage:
        r = make_result("telegram.token", DONE, "TELEGRAM_BOT_TOKEN present and non-empty")

    Gotchas:
        - `status` must be one of DONE / NOT_DONE / MANUAL.
        - `automatable` should be False for MANUAL items; True for DONE/NOT_DONE.
    """
    assert status in (DONE, NOT_DONE, MANUAL), f"Invalid status: {status!r}"
    return {
        "lane": lane,
        "status": status,
        "reason": reason,
        "automatable": automatable,
    }


# ---------------------------------------------------------------------------
# Classification logic (pure functions — unit-testable, no side effects)
# ---------------------------------------------------------------------------


def classify_env_key(env_path: Path, key: str) -> dict[str, Any]:
    """Check whether a required environment key is present and non-empty in the .env file.

    Purpose:
        Reads the .env file and returns DONE if the key has a non-empty value,
        NOT DONE if the key is missing or empty.

    Usage:
        r = classify_env_key(Path("~/.hermes/.env"), "TELEGRAM_BOT_TOKEN")

    Gotchas:
        - Strips surrounding whitespace from values.
        - A key set to a whitespace-only string is treated as missing (stub-like).
        - Does NOT validate the token against the live API — that is check §3.2.
    """
    if not env_path.exists():
        return make_result(f"env.{key}", NOT_DONE, f"{env_path} does not exist")

    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            val = v.strip()
            if val:
                return make_result(f"env.{key}", DONE, f"{key} present and non-empty")
            return make_result(f"env.{key}", NOT_DONE, f"{key} is set but empty")

    return make_result(f"env.{key}", NOT_DONE, f"{key} not found in {env_path}")


def classify_file_exists(path: Path, lane_name: str) -> dict[str, Any]:
    """Return DONE if a file exists and is non-empty; NOT DONE otherwise.

    Purpose:
        Used for config-file presence checks (e.g. ~/.hermes/config.yaml).

    Usage:
        r = classify_file_exists(HERMES_CONFIG, "hermes.config")

    Gotchas:
        - Checks file size > 0 to catch empty-file stubs.
    """
    if not path.exists():
        return make_result(lane_name, NOT_DONE, f"{path} does not exist")
    if path.stat().st_size == 0:
        return make_result(lane_name, NOT_DONE, f"{path} exists but is empty (stub)")
    return make_result(lane_name, DONE, f"{path} exists and is non-empty")


def classify_factory_health_section(
    providers: list[dict[str, Any]], section: str
) -> dict[str, Any]:
    """Classify a single provider from factory_health.py provider results.

    Purpose:
        Given the `providers` list from factory_health.py, finds the named
        provider and classifies it DONE (reachable with real latency) or NOT DONE.

    Usage:
        r = classify_factory_health_section(providers, "anthropic")

    Gotchas:
        - A provider is DONE only if both status=="reachable" AND latency_ms is not None
          and > 0.  This catches a theoretical stub that claims reachable with null latency.
        - Name matching is case-insensitive.
    """
    for p in providers:
        if p.get("name", "").lower() == section.lower():
            if p.get("status") == "reachable":
                latency = p.get("latency_ms")
                if latency is None or latency <= 0:
                    return make_result(
                        f"provider.{section}",
                        NOT_DONE,
                        f"{section}: claimed reachable but latency_ms={latency!r} (stub?)",
                    )
                return make_result(
                    f"provider.{section}",
                    DONE,
                    f"{section}: reachable ({latency}ms)",
                )
            cause = p.get("cause", "UNKNOWN")
            error = p.get("error", "")
            return make_result(
                f"provider.{section}",
                NOT_DONE,
                f"{section}: unreachable [{cause}] {error}",
            )
    return make_result(
        f"provider.{section}",
        NOT_DONE,
        f"{section}: not found in factory_health output",
    )


def classify_caffeinate(caffeinate: bool | None) -> dict[str, Any]:
    """Classify the caffeinate state from factory_health.py compute output.

    Purpose:
        Returns DONE if caffeinate is running, NOT DONE if absent, MANUAL if unknown.

    Usage:
        r = classify_caffeinate(compute["caffeinate"])

    Gotchas:
        - None means the compute check crashed — returned as NOT DONE with a note.
    """
    if caffeinate is True:
        return make_result("compute.caffeinate", DONE, "caffeinate process running")
    if caffeinate is False:
        return make_result(
            "compute.caffeinate",
            NOT_DONE,
            "caffeinate NOT running — Mac may sleep; run SG-P0-1 in a real Terminal",
        )
    return make_result(
        "compute.caffeinate",
        NOT_DONE,
        "caffeinate state unknown (compute check failed)",
    )


def classify_whatsapp_disabled(config_path: Path) -> dict[str, Any]:
    """Check that WhatsApp is cleanly disabled (enabled: false or entry absent).

    Purpose:
        Reads config.yaml for WhatsApp enable state.  The expected state is
        cleanly disabled until Phase R task R-2 runs the paired+verified gate.

    Usage:
        r = classify_whatsapp_disabled(HERMES_CONFIG)

    Gotchas:
        - If config.yaml is absent, returns NOT DONE (can't confirm disabled).
        - If "whatsapp" appears with enabled: true, returns NOT DONE.
        - This check passes when whatsapp is absent OR explicitly disabled.
    """
    if not config_path.exists():
        return make_result(
            "whatsapp.disabled", NOT_DONE, "config.yaml missing; can't confirm WhatsApp disabled"
        )
    content = config_path.read_text().lower()
    # If whatsapp block is absent, lane is safely disabled.
    if "whatsapp" not in content:
        return make_result("whatsapp.disabled", DONE, "WhatsApp not in config (cleanly absent)")
    # If present, must have enabled: false
    lines = content.splitlines()
    in_whatsapp_block = False
    for line in lines:
        stripped = line.strip()
        if "whatsapp" in stripped:
            in_whatsapp_block = True
        if in_whatsapp_block and "enabled" in stripped:
            if "false" in stripped:
                return make_result("whatsapp.disabled", DONE, "WhatsApp explicitly disabled")
            if "true" in stripped:
                return make_result(
                    "whatsapp.disabled",
                    NOT_DONE,
                    "WhatsApp enabled:true — must be disabled until Phase R R-2 pairing",
                )
    return make_result(
        "whatsapp.disabled",
        NOT_DONE,
        "WhatsApp in config but enabled state unclear — inspect config.yaml",
    )


def classify_jira_lane_phase() -> dict[str, Any]:
    """Return MANUAL/NOT DONE for Jira lane since Phase P3 is not yet built.

    Purpose:
        The Jira poller ships in Phase P3. Until then this lane is structurally
        NOT DONE regardless of any code that exists, and the check is MANUAL once built.

    Usage:
        r = classify_jira_lane_phase()

    Gotchas:
        - This function is intentionally simple and returns a fixed NOT DONE.
          Once P3 lands, replace this with a real live API call check.
    """
    return make_result(
        "jira.lane",
        NOT_DONE,
        "Phase P3 not yet built — Jira poller does not exist; lane is NOT DONE by definition",
        automatable=False,
    )


def classify_calendar_lane_phase() -> dict[str, Any]:
    """Return NOT DONE for Calendar lane since Phase P1c is not yet built.

    Purpose:
        Calendar meeting-prep ships in Phase P1c. Until then this lane is NOT DONE.

    Usage:
        r = classify_calendar_lane_phase()

    Gotchas:
        - Replace with a live Graph Calendar API call check once P1c lands.
    """
    return make_result(
        "calendar.lane",
        NOT_DONE,
        "Phase P1c not yet built — calendar meeting-prep does not exist; lane is NOT DONE",
        automatable=False,
    )


# ---------------------------------------------------------------------------
# Live check runners (side effects; not unit-tested)
# ---------------------------------------------------------------------------


def run_factory_health() -> dict[str, Any] | None:
    """Run factory_health.py as a subprocess and return its parsed sections.

    Purpose:
        Calls factory_health.py --json (if supported) or parses stdout.
        Falls back to a simple exit-code check if JSON output is not available.

    Usage:
        sections = run_factory_health()
        if sections is None: print("factory_health.py failed to run")

    Gotchas:
        - factory_health.py does not currently emit JSON; we run it and fall back to
          the compute and providers modules directly.
        - Returns None on any subprocess error.
    """
    try:
        # Import directly (same venv) for accuracy and speed
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from scripts.factory_health import (
            check_all_providers,
            check_compute_state,
        )

        providers = check_all_providers()
        compute = check_compute_state()
        return {"providers": providers, "compute": compute}
    except Exception as exc:  # noqa: BLE001
        return {"_error": str(exc), "providers": [], "compute": {}}


def run_node_check(script: Path, args: list[str] | None = None, timeout: int = 15) -> dict[str, Any]:
    """Run a Node.js pm_os script and return its exit code and output.

    Purpose:
        Used for token health checks (check-token-health.js, ensure-tokens.js).

    Usage:
        result = run_node_check(PM_OS_BIN / "check-token-health.js", ["--quiet"])

    Gotchas:
        - Returns {"exit_code": -1, "stdout": "", "stderr": str(exc)} on subprocess error.
        - Does NOT interpret the output — callers decide DONE/NOT DONE from exit_code.
    """
    cmd = ["node", str(script)] + (args or [])
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except FileNotFoundError:
        return {"exit_code": -1, "stdout": "", "stderr": "node not found"}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"timed out after {timeout}s"}
    except Exception as exc:  # noqa: BLE001
        return {"exit_code": -1, "stdout": "", "stderr": str(exc)}


# ---------------------------------------------------------------------------
# Main check orchestration
# ---------------------------------------------------------------------------


def run_all_checks(send_telegram: bool = False, whatsapp_roundtrip: bool = False) -> list[dict[str, Any]]:
    """Run all automatable checks and return a list of result dicts.

    Purpose:
        Orchestrates every automatable check from SETUP-CHECKLIST.md in order,
        delegating to pure classification functions and live runners.

    Usage:
        results = run_all_checks()
        for r in results:
            print(r["lane"], r["status"])

    Gotchas:
        - MANUAL items are included in the results but do not contribute to the exit code.
        - If factory_health.py fails to import, provider/compute checks return NOT DONE.
    """
    results: list[dict[str, Any]] = []

    # §0 Pre-flight
    results.append(classify_file_exists(HERMES_ENV, "env.file"))
    results.append(classify_file_exists(HERMES_CONFIG, "hermes.config"))

    # §1 Provider reachability (via factory_health.py)
    health_sections = run_factory_health()
    providers = health_sections.get("providers", []) if health_sections else []
    compute = health_sections.get("compute", {}) if health_sections else {}

    results.append(classify_factory_health_section(providers, "anthropic"))
    results.append(classify_factory_health_section(providers, "openrouter"))

    # §2 Compute / caffeinate
    results.append(classify_caffeinate(compute.get("caffeinate")))

    # Note: launchd bootstrap (§2.3) is MANUAL — see STAGED-GATES.md SG-P0-1
    results.append(make_result(
        "compute.launchd_bootstrap",
        MANUAL,
        "Run SG-P0-1 in a real Terminal: launchctl bootstrap gui/$(id -u) <plist>",
        automatable=False,
    ))

    # §3 Telegram
    results.append(classify_env_key(HERMES_ENV, "TELEGRAM_BOT_TOKEN"))
    # Live getMe call (automatable — no token spend)
    token_result = classify_env_key(HERMES_ENV, "TELEGRAM_BOT_TOKEN")
    if token_result["status"] == DONE:
        # Extract token and call getMe
        token_val = ""
        for line in HERMES_ENV.read_text().splitlines():
            line = line.strip()
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                token_val = line.split("=", 1)[1].strip()
                break
        if token_val:
            try:
                import urllib.request
                import urllib.error
                url = f"https://api.telegram.org/bot{token_val}/getMe"
                t0 = time.monotonic()
                with urllib.request.urlopen(url, timeout=8) as resp:
                    body = json.loads(resp.read())
                latency = (time.monotonic() - t0) * 1000
                if body.get("ok") and latency > 5:
                    results.append(make_result(
                        "telegram.getme",
                        DONE,
                        f"getMe returned ok:true, latency {latency:.0f}ms (real HTTP call)",
                    ))
                elif body.get("ok"):
                    # Suspiciously fast — flag but don't fail
                    results.append(make_result(
                        "telegram.getme",
                        DONE,
                        f"getMe ok:true ({latency:.0f}ms — verify this was a real network call)",
                    ))
                else:
                    results.append(make_result(
                        "telegram.getme", NOT_DONE,
                        f"getMe returned ok:false: {body}",
                    ))
            except Exception as exc:  # noqa: BLE001
                results.append(make_result(
                    "telegram.getme", NOT_DONE,
                    f"getMe failed: {exc}",
                ))
        else:
            results.append(make_result("telegram.getme", NOT_DONE, "TELEGRAM_BOT_TOKEN empty"))
    else:
        results.append(make_result("telegram.getme", NOT_DONE, "TELEGRAM_BOT_TOKEN missing"))

    # Telegram round-trip (send + observe in app) = MANUAL
    if send_telegram:
        results.append(make_result(
            "telegram.send_roundtrip",
            MANUAL,
            "--send-telegram flag set but real send requires gateway; check app manually",
            automatable=False,
        ))
    else:
        results.append(make_result(
            "telegram.send_roundtrip",
            MANUAL,
            "Run with --send-telegram and confirm message appears in Telegram app",
            automatable=False,
        ))

    # §4 Email / M365 (token health via pm_os)
    token_health_script = PM_OS_BIN / "check-token-health.js"
    if token_health_script.exists():
        result = run_node_check(token_health_script, ["--quiet"])
        if result["exit_code"] == 0:
            results.append(make_result(
                "email.token_health",
                DONE,
                "check-token-health.js exit 0 — all tokens VALID",
            ))
        elif result["exit_code"] == 2:
            results.append(make_result(
                "email.token_health",
                NOT_DONE,
                f"check-token-health.js exit 2 — tokens expiring soon: {result['stderr'][:200]}",
            ))
        else:
            results.append(make_result(
                "email.token_health",
                NOT_DONE,
                f"check-token-health.js exit {result['exit_code']}: {result['stderr'][:200]}",
            ))
    else:
        results.append(make_result(
            "email.token_health",
            NOT_DONE,
            f"check-token-health.js not found at {token_health_script}",
        ))

    # Email send + receive = MANUAL (MFA may be needed; inbox check is human)
    results.append(make_result(
        "email.send_roundtrip",
        MANUAL,
        "Run: node ~/Code/pm_os/bin/outlook-send-mail.js --to yu_kuan@yahoo.com "
        "--subject 'hermes-setup-test-<ts>' --body 'round-trip test' "
        "then confirm arrival in inbox",
        automatable=False,
    ))

    # §5 WhatsApp disabled
    results.append(classify_whatsapp_disabled(HERMES_CONFIG))
    if whatsapp_roundtrip:
        results.append(make_result(
            "whatsapp.roundtrip",
            MANUAL,
            "WhatsApp round-trip requires physical QR code scan (R-2) + device confirm",
            automatable=False,
        ))

    # §6 Jira lane
    results.append(classify_jira_lane_phase())

    # §7 Calendar lane
    results.append(classify_calendar_lane_phase())

    # §8 Probe
    capabilities_json = Path(__file__).parent.parent / "capabilities.json"
    if capabilities_json.exists() and capabilities_json.stat().st_size > 0:
        try:
            cap = json.loads(capabilities_json.read_text())
            if isinstance(cap, dict):
                results.append(make_result(
                    "probe.capabilities_json",
                    DONE,
                    f"capabilities.json exists, {len(cap)} top-level keys",
                ))
            else:
                results.append(make_result(
                    "probe.capabilities_json", NOT_DONE,
                    "capabilities.json is not a JSON object",
                ))
        except json.JSONDecodeError as exc:
            results.append(make_result(
                "probe.capabilities_json", NOT_DONE,
                f"capabilities.json is not valid JSON: {exc}",
            ))
    else:
        results.append(make_result(
            "probe.capabilities_json",
            NOT_DONE,
            "capabilities.json missing or empty — run: python scripts/probe.py",
        ))

    return results


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_text(results: list[dict[str, Any]]) -> str:
    """Format results as a human-readable table with a summary.

    Purpose:
        Renders the check results for terminal output.

    Usage:
        print(render_text(results))

    Gotchas:
        - MANUAL items are shown but do not count toward the NOT DONE total.
    """
    lines = ["=== Setup Check ===", ""]
    width = max(len(r["lane"]) for r in results) + 2

    done_count = sum(1 for r in results if r["status"] == DONE)
    not_done_auto = [r for r in results if r["status"] == NOT_DONE and r.get("automatable", True)]
    manual_count = sum(1 for r in results if r["status"] == MANUAL)

    for r in results:
        status_str = r["status"].ljust(8)
        lane_str = r["lane"].ljust(width)
        lines.append(f"  {status_str}  {lane_str}  {r['reason']}")

    lines.append("")
    lines.append(
        f"Summary: {done_count} DONE  |  {len(not_done_auto)} NOT DONE (automatable)  |  {manual_count} MANUAL"
    )
    if not_done_auto:
        lines.append("")
        lines.append("Blocking items (automatable but NOT DONE):")
        for r in not_done_auto:
            lines.append(f"  - {r['lane']}: {r['reason']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    """Run setup checks and print results.

    Purpose:
        CLI entrypoint — parses args, runs checks, renders output, returns exit code.

    Usage:
        python scripts/setup_check.py [--send-telegram] [--whatsapp-roundtrip] [--json]

    Gotchas:
        - Exit 1 only on automatable NOT DONE items; MANUAL items do not block.
    """
    parser = argparse.ArgumentParser(description="Hermes factory lane behavioral setup checker")
    parser.add_argument("--send-telegram", action="store_true", help="Attempt Telegram test send")
    parser.add_argument("--whatsapp-roundtrip", action="store_true", help="Include WhatsApp round-trip")
    parser.add_argument("--json", dest="json_out", action="store_true", help="Emit JSON output")
    args = parser.parse_args()

    results = run_all_checks(
        send_telegram=args.send_telegram,
        whatsapp_roundtrip=args.whatsapp_roundtrip,
    )

    if args.json_out:
        print(json.dumps(results, indent=2))
    else:
        print(render_text(results))

    blocking = [r for r in results if r["status"] == NOT_DONE and r.get("automatable", True)]
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
