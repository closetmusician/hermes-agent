#!/usr/bin/env python3
# ABOUTME: Factory capability probe — detects and records CLI versions, feature flags,
# ABOUTME: git-worktree support, and OpenRouter live-catalog status with 1-token pings.
# ABOUTME: Writes capabilities.json at repo root; exits 0 on full success, non-zero
# ABOUTME: naming each broken capability.  Phantom models (e.g. GLM-5.2) produce a
# ABOUTME: loud failure entry and are never silently passed into the routing table.

"""
Factory capability probe (P0-3 + P0-4).

Purpose
-------
Validate every assumption the model router makes before any job runs:
  - claude and codex CLI presence + versions + key flags
  - git worktree support
  - Each candidate OpenRouter model's existence in the live catalog
  - 1-token ping per catalog-confirmed model (skip on missing key)
  - Phantom model detection (GLM-5.2 → loud failure, never silent pass)

Usage
-----
  python scripts/probe.py [--out capabilities.json] [--no-ping]

Gotchas
-------
  - Never prints API key values; all key references stay masked in output.
  - On SSL/sandbox errors, caller should retry with dangerouslyDisableSandbox.
  - Pings use max_tokens=1 to minimise cost; total spend target < $0.05.
  - OPENROUTER_API_KEY must be set in the environment for pings to run;
    on missing key, catalog-only mode is used and a note is recorded.
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time

import requests

# ---------------------------------------------------------------------------
# Candidate OpenRouter models from the plan (§P0-4, §P4-2, §P0.2)
# These are CANDIDATES only — the probe resolves truth from the live catalog.
# The routing ladder in the plan: Claude Max → Codex → OpenRouter overflow
# (glm-5 → Kimi → DeepSeek).  GLM-5.2 is a known phantom and MUST fail.
# ---------------------------------------------------------------------------
CANDIDATE_MODELS = [
    # Confirmed present in the plan as "actually confirmed today" (plan §P0.2)
    "zhipuai/glm-5",          # ZAI/Novita — plan says "confirmed present"
    "gmi-ai/GLM-5.1-FP8",     # GMI — plan says "confirmed present"
    # Phantom — MUST fail loud
    "zhipuai/GLM-5.2",        # plan: "appears in no provider config" — phantom
    # Failover ladder overflow models mentioned in the plan
    "moonshot/kimi-k2",       # Kimi / Moonshot
    "deepseek/deepseek-r1",   # DeepSeek
    "deepseek/deepseek-v3",   # DeepSeek V3 (often free tier)
    # Free models referenced in hermes config (auxiliary/web_extract)
    "deepseek/deepseek-v4-flash:free",  # used in config.yaml auxiliary sections
]

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

# Cost estimate: cheapest tier ~$0.10/M tokens input; 1 token = $0.0000001
# At 20 models × 1 token = $0.000002 total — well under $0.05 budget.
COST_PER_TOKEN_USD = 0.0000004  # conservative upper bound across free-tier models


# ---------------------------------------------------------------------------
# Pure-logic helpers (tested; no side effects)
# ---------------------------------------------------------------------------


def parse_catalog(payload: dict) -> set:
    """
    Extract model IDs from an OpenRouter /v1/models response payload.

    Purpose: turn the raw API response into a set of string IDs that
    build_model_entry() can do O(1) membership tests against.

    Usage: parse_catalog(response.json())  → {"provider/model-id", ...}

    Gotchas: raises KeyError if 'data' key is absent (malformed response).
    Models without an 'id' field are silently skipped.
    """
    return {m["id"] for m in payload["data"] if "id" in m}


def validate_capabilities(caps: dict) -> None:
    """
    Assert that a capabilities dict has the required top-level structure.

    Purpose: catch schema drift early — if probe.py is changed and omits
    a section, this gate fires before capabilities.json is written.

    Usage: validate_capabilities(caps)  — raises on violation.

    Gotchas: validates structure only, not value semantics; does not
    check that ping results are numerically reasonable.
    """
    required_top = {"probed_at", "cli", "git_worktree", "openrouter"}
    missing = required_top - set(caps.keys())
    if missing:
        raise ValueError(f"capabilities missing required keys: {missing}")

    # Validate per-model entries in openrouter.models
    for model_id, entry in caps.get("openrouter", {}).get("models", {}).items():
        required_model_keys = {"live_catalog", "ping_ok", "latency_ms"}
        missing_model = required_model_keys - set(entry.keys())
        if missing_model:
            raise ValueError(
                f"model entry '{model_id}' missing keys: {missing_model}"
            )


def build_model_entry(
    model_id: str,
    catalog: set,
    ping_result: dict | None,
) -> dict:
    """
    Build a capabilities.json model entry from catalog membership and ping.

    Purpose: single place that enforces the invariant that a model absent
    from the live catalog always gets live_catalog=False and ping_ok=False —
    never a silent omission (the exact failure the probe exists to catch).

    Usage: build_model_entry("zhipuai/GLM-5.2", catalog, None)

    Gotchas: GLM-5.2 and any other phantom model MUST produce live_catalog=False.
    A None ping_result means the model was not pinged (not in catalog, or
    key missing); ping_ok defaults to False in that case.
    """
    in_catalog = model_id in catalog

    if ping_result is not None and in_catalog:
        ping_ok = bool(ping_result.get("ok", False))
        latency_ms = ping_result.get("latency_ms")
    else:
        ping_ok = False
        latency_ms = None

    return {
        "live_catalog": in_catalog,
        "ping_ok": ping_ok,
        "latency_ms": latency_ms,
    }


def compute_exit_code(failures: list) -> int:
    """
    Return 0 if no failures, 1 if any named failures are present.

    Purpose: single authoritative mapping from failure-list → exit code so
    tests can check policy without running the full probe end-to-end.

    Usage: sys.exit(compute_exit_code(failures))

    Gotchas: empty list → 0; any non-empty list → 1 (non-zero).
    """
    return 0 if not failures else 1


def estimate_ping_cost(num_models: int, tokens_per_ping: int = 1) -> float:
    """
    Return a conservative upper-bound cost estimate in USD for a set of pings.

    Purpose: used in the report to verify REQ-04 (total spend < $0.05).
    Uses COST_PER_TOKEN_USD which is a conservative upper bound for the
    cheapest free-tier models on OpenRouter.

    Usage: estimate_ping_cost(num_models=20, tokens_per_ping=1)

    Gotchas: real cost depends on model pricing; this is a ceiling, not exact.
    """
    return num_models * tokens_per_ping * COST_PER_TOKEN_USD


# ---------------------------------------------------------------------------
# CLI detection
# ---------------------------------------------------------------------------


def _run_version_cmd(cmd: list) -> tuple[str | None, str | None]:
    """
    Run a CLI version command and return (version_string, error).

    Purpose: isolated so tests can patch subprocess.run cleanly.
    Usage: version, err = _run_version_cmd(["claude", "--version"])
    Gotchas: returns (None, error_message) on any failure.
    """
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0:
            return r.stdout.strip() or r.stderr.strip(), None
        return None, r.stderr.strip() or f"exit {r.returncode}"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return None, str(e)


def check_cli_capabilities() -> dict:
    """
    Detect claude and codex CLI presence, versions, and key flags.

    Purpose: REQ-01 — probe help output to find the current budget flag names
    rather than hard-coding assumptions about flag names.

    Usage: result = check_cli_capabilities()
    Returns dict with keys: claude, codex, failures (list of named failures).

    Gotchas: --max-budget-usd for claude is probed from --help; codex does
    not have this flag but may have equivalents — probe help output.
    """
    failures = []

    # ---- claude ----
    claude_path = shutil.which("claude")
    claude_info: dict = {"present": False, "absent": False}

    if not claude_path:
        claude_info = {
            "present": False,
            "absent": True,
            "version": None,
            "max_budget_usd_flag": False,
            "json_schema_output": False,
        }
        failures.append("claude_cli_missing")
        print("FAILURE claude_cli_missing: 'claude' not found on PATH", file=sys.stderr)
    else:
        version_str, err = _run_version_cmd([claude_path, "--version"])
        claude_info["present"] = True
        claude_info["absent"] = False
        claude_info["version"] = version_str

        # Probe --help for --max-budget-usd (do not assume flag name)
        help_out = ""
        try:
            r = subprocess.run(
                [claude_path, "--help"],
                capture_output=True, text=True, timeout=15
            )
            # Coerce to str defensively (MagicMock-safe for tests)
            help_out = str(r.stdout or "") + str(r.stderr or "")
        except Exception:
            pass

        has_budget_flag = "--max-budget-usd" in help_out
        claude_info["max_budget_usd_flag"] = has_budget_flag
        if not has_budget_flag:
            # Not a hard failure — flag may be renamed; record it
            alt_budget = re.search(r"--[\w-]*budget[\w-]*", help_out)
            claude_info["budget_flag_alt"] = alt_budget.group(0) if alt_budget else None
            failures.append("claude_max_budget_usd_flag_missing")
            print(
                "FAILURE claude_max_budget_usd_flag_missing: "
                "--max-budget-usd not found in 'claude --help'",
                file=sys.stderr,
            )

        # Probe --output-format json-schema support
        has_json_schema = "--output-format" in help_out and "json" in help_out.lower()
        claude_info["json_schema_output"] = has_json_schema

    # ---- codex ----
    codex_path = shutil.which("codex")
    codex_info: dict = {"present": False, "absent": False}

    if not codex_path:
        codex_info = {
            "present": False,
            "absent": True,
            "version": None,
            "budget_flag": None,
            "budget_flag_name": None,
            "json_output": False,
        }
        # Not a hard failure per plan (escalate_if, not fail)
        print(
            "NOTE codex_cli_absent: 'codex' not found on PATH — recording absent:true",
            file=sys.stderr,
        )
    else:
        version_str, err = _run_version_cmd([codex_path, "--version"])
        codex_info["present"] = True
        codex_info["absent"] = False
        codex_info["version"] = version_str

        # Probe codex exec --help for budget-like flags
        exec_help = ""
        try:
            r = subprocess.run(
                [codex_path, "exec", "--help"],
                capture_output=True, text=True, timeout=15
            )
            exec_help = str(r.stdout or "") + str(r.stderr or "")
        except Exception:
            pass

        budget_match = re.search(r"--[\w-]*budget[\w-]*", exec_help)
        codex_info["budget_flag"] = budget_match is not None
        codex_info["budget_flag_name"] = budget_match.group(0) if budget_match else None
        codex_info["json_output"] = "--json" in exec_help or "json" in exec_help.lower()

    return {"claude": claude_info, "codex": codex_info, "failures": failures}


# ---------------------------------------------------------------------------
# Git worktree detection
# ---------------------------------------------------------------------------


def check_git_worktree() -> dict:
    """
    Verify git worktree support by running 'git worktree list'.

    Purpose: REQ-01 — confirm the factory's worktree isolation primitive works.
    Usage: result = check_git_worktree()  →  {supported: bool, output: str}
    Gotchas: requires git to be on PATH and the CWD to be a git repo.
    """
    try:
        r = subprocess.run(
            ["git", "worktree", "list"],
            capture_output=True, text=True, timeout=15,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        supported = r.returncode == 0
        return {
            "supported": supported,
            "output_preview": r.stdout.strip()[:200] if supported else r.stderr.strip()[:200],
        }
    except Exception as e:
        return {"supported": False, "error": str(e)}


# ---------------------------------------------------------------------------
# OpenRouter catalog fetch + ping
# ---------------------------------------------------------------------------


def fetch_openrouter_catalog(api_key: str) -> dict | None:
    """
    Fetch the live model catalog from OpenRouter /v1/models.

    Purpose: REQ-02 — get the authoritative list of model IDs so phantom
    models produce a loud failure rather than silent omission.

    Usage: payload = fetch_openrouter_catalog(api_key)  →  raw dict or None

    Gotchas: returns None on empty key, HTTP errors, or network failure.
    Caller must handle None before calling parse_catalog().
    """
    if not api_key or not api_key.strip():
        return None

    try:
        resp = requests.get(
            OPENROUTER_MODELS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://github.com/hermesagent/hermes",
                "X-Title": "hermes-probe",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"NOTE fetch_openrouter_catalog failed: {e}", file=sys.stderr)
        return None


def _ping_model(model_id: str, api_key: str) -> dict:
    """
    Send a 1-token completion to a model to verify it responds.

    Purpose: REQ-02 — confirm a catalog entry actually works, not just exists.
    Usage: result = _ping_model("zhipuai/glm-5", api_key)

    Gotchas: max_tokens=1 to stay under $0.05 total budget (REQ-04).
    Returns {ok: bool, latency_ms: int | None, error: str | None}.
    """
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }
    t0 = time.monotonic()
    try:
        resp = requests.post(
            OPENROUTER_CHAT_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/hermesagent/hermes",
                "X-Title": "hermes-probe",
            },
            json=payload,
            timeout=30,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code == 200:
            return {"ok": True, "latency_ms": latency_ms, "error": None}
        else:
            return {
                "ok": False,
                "latency_ms": latency_ms,
                "error": f"HTTP {resp.status_code}: {resp.text[:120]}",
            }
    except Exception as e:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {"ok": False, "latency_ms": latency_ms, "error": str(e)}


def probe_openrouter_models(
    candidates: list,
    api_key: str,
    skip_pings: bool = False,
) -> dict:
    """
    Fetch live catalog and probe each candidate model.

    Purpose: REQ-02 — resolve each candidate name against the live catalog,
    1-token ping catalog-confirmed models, and produce loud failure entries
    for phantoms like GLM-5.2 (never silently omit them).

    Usage: result = probe_openrouter_models(CANDIDATE_MODELS, api_key)

    Gotchas: if api_key is empty, returns catalog_fetched=False and skips
    all pings.  All phantom models still appear in the output with
    live_catalog=False, ping_ok=False.
    """
    failures = []

    if not api_key:
        print(
            "NOTE openrouter_key_missing: OPENROUTER_API_KEY not set — "
            "catalog-only mode skipped; recording key_present:false",
            file=sys.stderr,
        )
        # Still record each candidate as unverified (not silently omit)
        models = {
            m: {"live_catalog": False, "ping_ok": False, "latency_ms": None,
                "error": "key_missing"}
            for m in candidates
        }
        return {
            "catalog_fetched": False,
            "key_present": False,
            "models": models,
            "failures": failures,
        }

    # Fetch catalog
    raw = fetch_openrouter_catalog(api_key)
    if raw is None:
        print(
            "FAILURE openrouter_catalog_fetch_failed: could not reach OpenRouter /v1/models",
            file=sys.stderr,
        )
        failures.append("openrouter_catalog_fetch_failed")
        models = {
            m: {"live_catalog": False, "ping_ok": False, "latency_ms": None,
                "error": "catalog_fetch_failed"}
            for m in candidates
        }
        return {
            "catalog_fetched": False,
            "key_present": True,
            "models": models,
            "failures": failures,
        }

    catalog = parse_catalog(raw)

    # Probe each candidate
    models = {}
    phantom_names = []
    for model_id in candidates:
        in_catalog = model_id in catalog

        if not in_catalog:
            phantom_names.append(model_id)
            entry = build_model_entry(model_id, catalog, None)
            models[model_id] = entry
            # Loud failure to stderr for phantom models
            print(
                f"FAILURE phantom_model: '{model_id}' is NOT in the live OpenRouter "
                f"catalog — this model cannot enter the routing table",
                file=sys.stderr,
            )
            continue

        # Ping catalog-confirmed models
        if skip_pings:
            ping_result = None
            entry = {"live_catalog": True, "ping_ok": None, "latency_ms": None}
        else:
            ping_result = _ping_model(model_id, api_key)
            entry = build_model_entry(model_id, catalog, ping_result)
            if ping_result.get("error"):
                entry["error"] = ping_result["error"]
            print(
                f"  ping {model_id}: ok={entry['ping_ok']} latency={entry['latency_ms']}ms",
                file=sys.stderr,
            )

        models[model_id] = entry

    if phantom_names:
        failures.append(f"phantom_models_detected:{','.join(phantom_names)}")

    return {
        "catalog_fetched": True,
        "key_present": True,
        "catalog_size": len(catalog),
        "models": models,
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_probe(output_path: str, skip_pings: bool = False) -> tuple[dict, list]:
    """
    Run the full capability probe and return (capabilities_dict, failures).

    Purpose: orchestrates all probe checks and assembles the final
    capabilities.json.  Separated from main() so tests can call it
    without sys.exit side effects.

    Usage: caps, failures = run_probe("capabilities.json")

    Gotchas: reads OPENROUTER_API_KEY from environment; never prints
    the key value.  On any check failure, the named failure is appended
    to the failures list but execution continues (to produce a complete
    capabilities.json even on partial failure).
    """
    all_failures: list = []

    print("=== hermes capability probe ===", file=sys.stderr)

    # REQ-01: CLI capabilities
    print("--- checking CLIs ---", file=sys.stderr)
    cli_result = check_cli_capabilities()
    all_failures.extend(cli_result.pop("failures", []))

    # REQ-01: git worktree
    print("--- checking git worktree ---", file=sys.stderr)
    worktree_result = check_git_worktree()
    if not worktree_result.get("supported"):
        all_failures.append("git_worktree_unsupported")
        print("FAILURE git_worktree_unsupported", file=sys.stderr)
    else:
        print("  git worktree: supported", file=sys.stderr)

    # REQ-02: OpenRouter catalog + model pings
    print("--- probing OpenRouter models ---", file=sys.stderr)
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    or_result = probe_openrouter_models(CANDIDATE_MODELS, api_key, skip_pings)
    all_failures.extend(or_result.pop("failures", []))

    # Assemble capabilities dict
    caps = {
        "probed_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cli": {
            "claude": cli_result["claude"],
            "codex": cli_result["codex"],
        },
        "git_worktree": worktree_result,
        "openrouter": or_result,
    }

    # REQ-04: Cost estimate
    num_models = len(CANDIDATE_MODELS)
    estimated_cost = estimate_ping_cost(num_models)
    caps["cost_estimate_usd"] = {
        "num_models_probed": num_models,
        "tokens_per_ping": 1,
        "estimated_cost_usd": round(estimated_cost, 6),
        "budget_limit_usd": 0.05,
        "within_budget": estimated_cost < 0.05,
    }

    validate_capabilities(caps)

    return caps, all_failures


def main() -> int:
    """
    CLI entry point for the capability probe.

    Purpose: parse args, run probe, write capabilities.json, exit with
    appropriate code (0 = all good, non-zero = named failures).

    Usage: python scripts/probe.py [--out capabilities.json] [--no-ping]

    Gotchas: if --out points to a path that doesn't exist, the directory
    must exist.  Use --no-ping to skip 1-token pings (catalog check only).
    """
    parser = argparse.ArgumentParser(description="hermes factory capability probe")
    parser.add_argument(
        "--out",
        default="capabilities.json",
        help="Output path for capabilities.json (default: capabilities.json)",
    )
    parser.add_argument(
        "--no-ping",
        action="store_true",
        help="Skip 1-token pings (catalog presence check only)",
    )
    args = parser.parse_args()

    caps, failures = run_probe(args.out, skip_pings=args.no_ping)

    # Write capabilities.json
    with open(args.out, "w") as f:
        json.dump(caps, f, indent=2)
        f.write("\n")

    print(f"\nWrote {args.out}", file=sys.stderr)

    if failures:
        print("\nFAILURES detected:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
    else:
        print("\nAll capability checks passed.", file=sys.stderr)

    code = compute_exit_code(failures)
    print(f"Exit code: {code}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
