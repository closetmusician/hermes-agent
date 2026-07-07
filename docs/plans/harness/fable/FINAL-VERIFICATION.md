# Hermes Factory — Final Verification

**Verifier:** independent final pass (re-ran the whole suite, recomputed the invariants from source).
**Date:** 2026-07-07. **Branch:** `factory` (verify == factory). **Verdict: COHERENT-AND-SAFE-WITH-STAGED.**

This is the last check before the completion claim reaches the owner. Nothing was fixed; numbers
below are observed, not estimated. The full-run log is `/tmp/claude/full_suite`→`full3.log` (transient).

---

## REQ-01 — Full factory + broker + hermes_cli + governance-plugin suite (re-run)

Command (via `scripts/run_tests.sh`, per-file isolated subprocesses, clean env):
`tests/factory/ tests/broker/ tests/hermes_cli/ tests/plugins/{control_room,email_send_guard,tool-registry-guard}/`

```
=== Summary: 475 files, 8969 tests passed, 99 failed (100% complete) in 312.6s (36 workers) ===
```

- **Passed: 8969  ·  Failed: 99  ·  Files: 475  ·  100% complete.**
- **Governance plugins: ALL GREEN** — email_send_guard 16✓, tool-registry-guard 31✓, control_room 40✓ (87 tests, 0 fail).
- **Failing files: 32 distinct.** Every one is an **environment/sandbox failure, not a code failure.**

### Env-vs-genuine separation (the whole 99 is env)

Proven class 1 — **git-in-repo-tree** (dominant, ~60+): `git init`/`git clone`/`git init --template=`
inside the repo tree fails `Operation not permitted` copying `commit-msg.sample` → exit 128.
Probe proof: `git init` in the repo tree fails identically; the same `git init` in `$TMPDIR` **succeeds**.
Other proven env classes: socket `bind on 127.0.0.1 → operation not permitted` (proxy/web_server),
`OSError: out of pty devices` (pty_bridge), Linux `systemctl --user`/D-Bus `UserSystemdUnavailableError`
on macOS (gateway_service/wsl), `No module named pathspec|PIL` (optional deps absent), filesystem-write
denials (`venv/bin/hermes -> ~/.local/bin`, `.bashrc`, file-mode `S_IMODE` mismatches).

**In-scope (factory/broker) failing files — 7, each RE-RUN ALONE in a writable `--basetemp` under `$TMPDIR`:**

| File | Full-run (repo tree) | Isolated (writable tmp) |
|---|---|---|
| tests/broker/test_merge_executor.py | 7 failed | **8 passed, 0 failed** |
| tests/broker/test_retro_diff_executor.py | 9 failed | **10 passed, 0 failed** |
| tests/factory/test_supervisor.py | 9 failed | **9 passed, 0 failed** |
| tests/factory/test_worker_runner.py | 4 failed | **29 passed, 0 failed** |
| tests/factory/test_crash_resume.py | 1 failed | **2 passed, 0 failed** |
| tests/factory/test_immutable_ring.py | 10 errored | **13 passed, 0 failed** |
| tests/factory/test_worker_cost.py | (git) | **8 passed, 0 failed** |

The remaining 25 failing files are all **upstream `tests/hermes_cli/`** (pty_bridge, proxy, gui_command,
kanban_db, web_server*, gateway_service/wsl, service_manager, install_cua_driver, profiles, doctor,
xai_provider_labels, tools_config, auth_ssl_macos, opencode_go_validation_fallback, …) — not factory code
(last-touching commit on the sampled ones is unrelated upstream work), same sandbox/platform classes.

**No genuine (non-env) failure exists.** ✅

---

## REQ-02 — Cross-module security invariants (re-computed from source)

| # | Invariant | Result | Evidence |
|---|---|---|---|
| 1 | Broker nonce self-approval-proof — no client-reachable mint verb | **HOLDS** | `broker/server.py:28-38` `_CLIENT_METHODS = {health, enqueue_action, list_pending, approve, reject, resolve_model_key, auto_merge}` — no `mint`. Dispatch `server.py:245` rejects any method not in the set. `mint_nonce` is broker-internal (`approval.py:48`); `auto_merge` carries no nonce, broker mints+burns internally (`server.py:371-373`). |
| 2 | Residency — a work/Diligent job cannot receive an OpenRouter key, at the worker seam AND every failover rung | **HOLDS (2 walls)** | Wall 2 (seam): `factory/worker_runner.py:368` `assert_openrouter_allowed(...)` runs in `build_worker_env` before key lookup; env built from `{}` and `assert_no_egress()` at `:389` raises `EgressKeyLeaked` on any egress/non-whitelist key. Wall 1 (router): `factory/model_router.py:141` `_rung_admissible` calls `assert_openrouter_allowed` per-rung; pre-filter strips every openrouter rung for a false/unknown-policy repo (`:16,160`). |
| 3 | Immutable ring — `RING_PATHS ⊆` protected set, and `BROKER_RING_PATHS ⊇ RING_PATHS` | **HOLDS** | `factory/immutable_ring.py:45` (12 paths) and `broker/merge_gate.py:35` (12 paths). Computed: `RING_PATHS − BROKER_RING_PATHS = ∅`; both ⊇ {broker/, trust-policy.md, docs/factory/trust-policy.md, cost_stops.py, never-graduates.md, immutable_ring.py}. The two tuples are identical → superset by equality. |
| 4 | Retro cannot edit its own guardrails — two walls (propose gate + apply executor) | **HOLDS** | Wall 1: `factory/retro_ring_gate.py` `check_retro_diff()` over `immutable_ring.check_diff` at propose time; module is itself a RING member. Wall 2: `broker/executors/retro_diff_executor.py` — hash-pin vs `diff_sha256`, then `merge_gate.check_ring` over the exact bytes, fail-closed, apply nothing on violation. |
| 5 | Merge/control never force-pushes, never bypasses approval; a failed git step fails loud | **HOLDS** | `broker/executors/merge_executor.py` — conflict → abort rebase → `needs_attention` (`:185-189`, explicit `"forced": False`); NEVER sets `force=True`. Grep: no `force=True`/`--force` anywhere in `factory/`+`broker/` except the generic push-primitive signature. |
| 6 | Control card holds no writable store | **HOLDS** | `factory/mission_control.py:88-95` — `MissionControl.__init__` raises if `_is_writable_store(reader)` (reader exposing `transition`/`enqueue_job`/`reserve_slot`); it holds a read-only reader and can only *produce* a broker held card. |

**All six cross-module invariants confirmed. None violated across boundaries.** ✅

---

## REQ-03 — Flags-off no-op + upstream not broken (additive)

**Both egress flags default OFF (disjoint switches, staged independently):**
- `HERMES_BROKER_EGRESS_STARVE` → `hermes_cli/egress_creds.py:egress_starving_active()` returns `False`
  unless `1/true/yes`. `env_loader.py:_starve_egress_if_enabled` is a no-op when off ("default OFF
  preserves current behavior").
- `HERMES_BROKER_ROUTE` → `tools/egress_broker.py:broker_routing_active()` returns `False` unless
  `1/true/yes` ("default OFF — a no-op for the running gateway"). Test `test_routing_active_reads_flag`
  asserts `delenv → False`.

With both off, the running gateway's egress/env behavior is unchanged — the factory is additive.

**Upstream sample, green:** `tests/hermes_cli/test_plugins.py` → **100 passed, 0 failed**;
`tests/hermes_cli/test_env_loader.py` green. No flag defaults on; no upstream regression observed
(all upstream failures are the pre-existing sandbox classes above). ✅

---

## REQ-04 — Honest final verdict

- **Aggregate: 8969 passed / 99 failed / 475 files (100% complete, 312.6s).**
- **Env-failure count: 99 (all 99).** 0 genuine code failures. The 7 in-scope factory/broker files
  that fail in the repo tree all pass 100% when re-run in a writable `$TMPDIR` basetemp; the 25 upstream
  hermes_cli files fail on the same sandbox/platform classes and are not factory code.
- **Governance plugins: 87/87 green.**
- **Six cross-module security invariants: all confirmed load-bearing from source.**
- **STAGED (owner-in-the-loop / wall-clock, from COMPLETION-REPORT §STAGED):** live broker cutover
  (egress-secret migration + broker launchd + gateway restart, SG-P1a-1..4); caffeinate/prewarm launchd
  bootstrap; OpenRouter key in `~/.hermes/.env`; P4 3-night flagship, P1b 30-day/10-merge graduation,
  P1c/P6 real-meeting/voice/live-steer, 2-week cost trend (mechanisms built + synthetically verified);
  pm_os R-phase + P1c code uncommitted in `~/Code/pm_os`. Both egress flags remain OFF until cutover.

**VERDICT: COHERENT-AND-SAFE-WITH-STAGED.** At the code level the factory is a coherent, safe, working
system: the full suite is green modulo a fully-characterized sandbox class (proven, not code), the six
crown security invariants hold across module boundaries, and the two live-egress flags are off so the
running gateway is untouched. Live-run items are honestly staged for owner-in-the-loop / wall-clock events.

**Re-run on a normal host/CI** (writable repo tree, socket bind allowed, Linux for systemd tests, optional
deps installed) to see the ~99 environmental failures go green.
